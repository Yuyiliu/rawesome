{-# OPTIONS_GHC -Wall #-}

module Plotter ( newChannel, runPlotter, makeAccessors ) where

import qualified Control.Concurrent as CC
import Data.Sequence ( (|>) )
import qualified Data.Sequence as S
import Data.Time ( getCurrentTime, diffUTCTime )
import Graphics.UI.Gtk ( AttrOp( (:=) ) )
import qualified Graphics.UI.Gtk as Gtk
import System.Glib.Signals ( on )
import Text.Read ( readMaybe )

import Accessors ( makeAccessors )
import PlotTypes ( Channel(..), PbTree, pbTreeToTree )
import GraphWidget ( newGraph )

data ListView = ListView { lvChan :: Channel
                         , lvMaxHist :: Int
                         }

newChannel :: String -> PbTree a -> IO (Channel, CC.Chan a)
newChannel name pbTree = do
  time0 <- getCurrentTime
  
  seqChan <- CC.newChan
  seqMv <- CC.newMVar S.empty
  maxHistMv <- CC.newMVar (10000 :: Int)

  let serverLoop k = do
              -- wait until a new message is written to the Chan
        newMsg <- CC.readChan seqChan
        -- grab the timestamp
        time <- getCurrentTime
        -- append this to the Seq in the MVar, dropping the excess old messages
        maxNum <- CC.readMVar maxHistMv
        let f seq0 = return $ S.drop (S.length seq0 + 1 - maxNum) (seq0 |> (newMsg, k, diffUTCTime time time0))
        CC.modifyMVar_ seqMv f
        -- loop forever
        serverLoop (k+1)
  
  serverTid <- CC.forkIO $ serverLoop 0
  let retChan = Channel { chanName = name
                        , chanGetters = pbTreeToTree name pbTree
                        , chanSeq = seqMv
                        , chanMaxHist = maxHistMv
                        , chanServerThreadId = serverTid
                        }
  return (retChan, seqChan)

runPlotter :: [Channel] -> [CC.ThreadId] -> IO ()
runPlotter channels backgroundThreadsToKill = do
  _ <- Gtk.initGUI

  -- start the main window
  win <- Gtk.windowNew
  _ <- Gtk.set win [ Gtk.containerBorderWidth := 8
                   , Gtk.windowTitle := "Plot-ho-matic"
                   ]

  -- on close, kill all the windows and threads
  graphWindowsToBeKilled <- CC.newMVar []
  let killEverything = do
        gws <- CC.readMVar graphWindowsToBeKilled
        mapM_ Gtk.widgetDestroy gws
        mapM_ CC.killThread backgroundThreadsToKill
        mapM_ (CC.killThread . chanServerThreadId) channels
        Gtk.mainQuit
  _ <- Gtk.onDestroy win killEverything

  --------------- main widget -----------------
  -- button to clear history
  buttonClear <- Gtk.buttonNewWithLabel "clear history"
  _ <- Gtk.onClicked buttonClear $ do
    let clearChan (Channel {chanSeq=cs}) = CC.swapMVar cs S.empty >> return ()
    mapM_ clearChan channels

  -- list of channels
  chanWidget <- newChannelWidget channels graphWindowsToBeKilled

  -- vbox to hold buttons
  vbox <- Gtk.vBoxNew False 4
  Gtk.set vbox [ Gtk.containerChild := buttonClear
               , Gtk.containerChild := chanWidget
               ]

  -- add widget to window and show
  _ <- Gtk.set win [ Gtk.containerChild := vbox ]
  Gtk.widgetShowAll win
  Gtk.mainGUI


-- the list of channels
newChannelWidget :: [Channel] -> CC.MVar [Gtk.Window] -> IO Gtk.TreeView
newChannelWidget channels graphWindowsToBeKilled = do
  -- create a new tree model
  let toListView ch = do
        k <- CC.readMVar $ chanMaxHist ch
        return ListView { lvChan = ch, lvMaxHist = k }
  model <- mapM toListView channels >>= Gtk.listStoreNew
  treeview <- Gtk.treeViewNewWithModel model
  Gtk.treeViewSetHeadersVisible treeview True

  -- add some columns
  col0 <- Gtk.treeViewColumnNew
  col1 <- Gtk.treeViewColumnNew
  col2 <- Gtk.treeViewColumnNew

  Gtk.treeViewColumnSetTitle col0 "channel"
  Gtk.treeViewColumnSetTitle col1 "history"
  Gtk.treeViewColumnSetTitle col2 "new"

  renderer0 <- Gtk.cellRendererTextNew
  renderer1 <- Gtk.cellRendererTextNew
  renderer2 <- Gtk.cellRendererToggleNew

  Gtk.cellLayoutPackStart col0 renderer0 True
  Gtk.cellLayoutPackStart col1 renderer1 True
  Gtk.cellLayoutPackStart col2 renderer2 True

  Gtk.cellLayoutSetAttributes col0 renderer0 model $ \lv -> [ Gtk.cellText := chanName (lvChan lv)]
  Gtk.cellLayoutSetAttributes col1 renderer1 model $ \lv -> [ Gtk.cellText := show (lvMaxHist lv)
                                                            , Gtk.cellTextEditable := True
                                                            ]
  Gtk.cellLayoutSetAttributes col2 renderer2 model $ const [ Gtk.cellToggleActive := False]

  
  _ <- Gtk.treeViewAppendColumn treeview col0
  _ <- Gtk.treeViewAppendColumn treeview col1
  _ <- Gtk.treeViewAppendColumn treeview col2

  -- spawn a new graph when a checkbox is clicked
  _ <- on renderer2 Gtk.cellToggled $ \pathStr -> do
    let (i:_) = Gtk.stringToTreePath pathStr
    lv <- Gtk.listStoreGetValue model i
    graphWin <- newGraph (lvChan lv)
    
    -- add this window to the list to be killed on exit
    CC.modifyMVar_ graphWindowsToBeKilled (return . (graphWin:))


  -- how long to make the history
  _ <- on renderer1 Gtk.edited $ \treePath txt -> do
    let (i:_) = treePath
    lv <- Gtk.listStoreGetValue model i
    putStrLn $ "history len: " ++ txt
    case readMaybe txt of
      Nothing -> do
        putStrLn $ "invalid non-integer range entry: " ++ txt
        k0 <- CC.readMVar $ chanMaxHist (lvChan lv)
        Gtk.listStoreSetValue model i (lv {lvMaxHist = k0})
      Just k -> if k < 0
                then do
                  putStrLn $ "invalid negative range entry: " ++ txt
                  k0 <- CC.readMVar $ chanMaxHist (lvChan lv)
                  Gtk.listStoreSetValue model i (lv {lvMaxHist = k0})
                else do
                  _ <- CC.swapMVar (chanMaxHist (lvChan lv)) k
                  return ()

  return treeview
