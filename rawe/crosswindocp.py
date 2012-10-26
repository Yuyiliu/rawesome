import copy
import casadi as C
import matplotlib.pyplot as plt
import numpy
from numpy import pi
import zmq

from collocation import Coll,boundsFeedback
from config import readConfig
import kite_pb2
import kiteproto
import model

x0 = C.DMatrix( [ 1.154244772411
                , -0.103540608242
                , -0.347959211327
                , 0.124930983341
                , 0.991534857363
                , 0.035367725910
                , 0.316039689643
                , -0.073559821379
                , 0.945889986864
                , 0.940484536806
                , -0.106993361072
                , -0.322554269411
                , 0.000000000000
                , 0.000000000000
                , 0.000000000000
                , 0.137035790811
                , 3.664945343102
                , -1.249768772258
                , 0.000000000000
                , 3.874600000000
                , 0.0
                ])
x0=C.veccat([x0,C.sqrt(C.sumAll(x0[0:2]*x0[0:2])),0])

oldKites = []

def setupOcp(dae,conf,publisher,nk=50,nicp=1,deg=4):
    ocp = Coll(dae, nk=nk,nicp=nicp,deg=deg)
                   
    # constrain invariants
    def invariantErrs():
        dcm = C.horzcat( [ C.veccat([dae.x('e11'), dae.x('e21'), dae.x('e31')])
                         , C.veccat([dae.x('e12'), dae.x('e22'), dae.x('e32')])
                         , C.veccat([dae.x('e13'), dae.x('e23'), dae.x('e33')])
                         ] ).trans()
        err = C.mul(dcm.trans(), dcm)
        dcmErr = C.veccat([ err[0,0]-1, err[1,1]-1, err[2,2]-1, err[0,1], err[0,2], err[1,2] ])
        f = C.SXFunction( [dae.xVec(),dae.uVec(),dae.pVec()]
                        , [dae.output('c'),dae.output('cdot'),dcmErr]
                        )
        f.setOption('name','invariant errors')
        f.init()
        return f

    [c0,cdot0,dcmError0] = invariantErrs().call([ocp.xVec(0),ocp.uVec(0),ocp.pVec()])
    ocp.constrain(c0,'==',0)
    ocp.constrain(cdot0,'==',0)
    ocp.constrain(dcmError0,'==',0)

    # constrain airspeed
    def constrainAirspeedAlphaBeta():
        f = C.SXFunction( [dae.xVec(),dae.uVec(),dae.pVec()]
                        , [dae.output('airspeed'),dae.output('alpha(deg)'),dae.output('beta(deg)')]
                        )
        f.setOption('name','airspeed/alpha/beta')
        f.init()

        for k in range(0,nk):
            [airspeed,alphaDeg,betaDeg] = f.call([ocp.xVec(k),ocp.uVec(k),ocp.pVec()])
            ocp.constrain(airspeed,'>=',10)
            ocp.constrainBnds(alphaDeg,(-5,10))
            ocp.constrainBnds(betaDeg,(-10,10))
    constrainAirspeedAlphaBeta()

    # bounds
    ocp.bound('aileron',(-0.04,0.04))
    ocp.bound('elevator',(-0.1,0.1))

    ocp.bound('x',(-200,200))
    ocp.bound('y',(-200,200))
    ocp.bound('z',(0.5,200))
    ocp.bound('r',(1,30))
    ocp.bound('dr',(-10,10))
    ocp.bound('ddr',(-2.5,2.5))

    for e in ['e11','e21','e31','e12','e22','e32','e13','e23','e33']:
        ocp.bound(e,(-1.1,1.1))

    for d in ['dx','dy','dz']:
        ocp.bound(d,(-70,70))

    for w in ['w1','w2','w3']:
        ocp.bound(w,(-4*pi,4*pi))

    ocp.bound('delta',(-10*pi/180, 10*pi/180))
    ocp.bound('ddelta',(-4*pi,4*pi))
    ocp.bound('tc',(-100000,100000))
    ocp.bound('endTime',(0.5,5))
    ocp.bound('w0',(10,10))
    ocp.bound('winch_energy',(-1e6,1e6))

    # boundary conditions
    ocp.bound('winch_energy',(0,0),timestep=0,quiet=True)
    
    # make it periodic
    for name in [ #"x"   # state 0
                  "y"   # state 1
                , "z"   # state 2
#                , "e11" # state 3
#                , "e12" # state 4
#                , "e13" # state 5
#                , "e21" # state 6
#                , "e22" # state 7
#                , "e23" # state 8
#                , "e31" # state 9
#                , "e32" # state 10
#                , "e33" # state 11
#                , "dx"  # state 12
                , "dy"  # state 13
                , "dz"  # state 14
                , "w1"  # state 15
                , "w2"  # state 16
                , "w3"  # state 17
                , "delta" # state 18
                , "ddelta" # state 19
                , "r" # state 20
                , "dr" # state 21
                ]:
        ocp.constrain(ocp.lookup(name,timestep=0),'==',ocp.lookup(name,timestep=-1))

    # line angle constraints
    def lineAngleConstraints():
        def getCosAngle(k):
            r31 = ocp.lookup('e31',timestep=k)
            r32 = ocp.lookup('e32',timestep=k)
            r33 = ocp.lookup('e33',timestep=k)

            x = ocp.lookup('x',timestep=k)
            y = ocp.lookup('y',timestep=k)
            z = ocp.lookup('z',timestep=k)
            
#            r = ocp.lookup('r',timestep=k)
            r = C.sqrt(x*x + y*y + z*z)
            
            return (r31*x + r32*y + r33*z)/r

        for k in range(0,nk+1):
            ocp.constrain(getCosAngle(k),'>=',C.cos(80*pi/180))
    lineAngleConstraints()

    # euler angle periodic constraints
    def periodicEulers():
        def getEuler(k):
            r11 = ocp.lookup('e11',timestep=k)
            r12 = ocp.lookup('e12',timestep=k)
            mr13 = -ocp.lookup('e13',timestep=k)
#            mr13 -- nan protect
#              | mr13' >  1 =  1
#              | mr13' < -1 = -1
#              | otherwise = mr13'
            r23 = ocp.lookup('e23',timestep=k)
            r33 = ocp.lookup('e33',timestep=k)
          
            yaw   = C.arctan2(r12,r11)
            pitch = C.arcsin(mr13)
            roll  = C.arctan2(r23,r33)
            return (yaw,pitch,roll)
        (yaw0,pitch0,roll0) = getEuler(0)
        (yawF,pitchF,rollF) = getEuler(-1)
        ocp.constrain(yaw0,'==',yawF)
        ocp.constrain(pitch0,'==',pitchF)
        ocp.constrain(roll0,'==',rollF)
    periodicEulers()

    # objective function
    obj = 0
    for k in range(nk):
        u = ocp.uVec(k)
        ddr = ocp.lookup('ddr',timestep=k)
        tc = ocp.lookup('tc',timestep=k)
        aileron = ocp.lookup('aileron',timestep=k)
        elevator = ocp.lookup('elevator',timestep=k)
        
        aileronSigma = 0.1
        elevatorSigma = 0.1
        torqueSigma = 1000.0
        ddrSigma = 5.0
        
#        tc = tc - 390

        ailObj = aileron*aileron / (aileronSigma*aileronSigma)
        eleObj = elevator*elevator / (elevatorSigma*elevatorSigma)
        winchObj = ddr*ddr / (ddrSigma*ddrSigma)
        torqueObj = tc*tc / (torqueSigma*torqueSigma)
        
        obj += ailObj + eleObj + winchObj + torqueObj
    ocp.setObjective( C.sumAll(obj)*1e-8 + ocp.lookup('winch_energy',timestep=-1)/ocp.lookup('endTime') )

    # zero mq setup
    # callback function
    class MyCallback:
        def __init__(self):
            self.iter = 0 
        def __call__(self,f,*args):
            self.iter = self.iter + 1
            xOpt = numpy.array(f.input(C.NLP_X_OPT))

            opt = ocp.devectorize(xOpt)
            xup = opt['vardict']
            
            kiteProtos = []
            for k in range(0,nk):
                j = nicp*(deg+1)*k
                kiteProtos.append( kiteproto.toKiteProto(C.DMatrix(opt['x'][:,j]),C.DMatrix(opt['u'][:,j]),C.DMatrix(opt['p']), conf['kite']['zt'], conf['carousel']['rArm']) )
#            kiteProtos = [kiteproto.toKiteProto(C.DMatrix(opt['x'][:,k]),C.DMatrix(opt['u'][:,k]),C.DMatrix(opt['p']), conf['kite']['zt'], conf['carousel']['rArm']) for k in range(opt['x'].shape[1])]
            
            mc = kite_pb2.MultiCarousel()
            mc.css.extend(list(kiteProtos+oldKites))
            
            mc.messages.append("endTime: "+str(xup['endTime']))
            mc.messages.append("w0: "+str(xup['w0']))
            mc.messages.append("iter: "+str(self.iter))

            # bounds feedback
#            lbx = ocp.solver.input(C.NLP_LBX)
#            ubx = ocp.solver.input(C.NLP_UBX)
#            violations = boundsFeedback(xOpt,lbx,ubx,ocp.bndtags,tolerance=1e-9)
#            for name in violations:
#                violmsg = "violation!: "+name+": "+str(violations[name])
#                mc.messages.append(violmsg)
            
            publisher.send_multipart(["multi-carousel", mc.SerializeToString()])


    # solver
    solverOptions = [ ("expand_f",True)
                    , ("expand_g",True)
                    , ("generate_hessian",True)
#                     ,("qp_solver",C.NLPQPSolver)
#                     ,("qp_solver_options",{'nlp_solver': C.IpoptSolver, "nlp_solver_options":{"linear_solver":"ma57"}})
                    , ("linear_solver","ma57")
                    , ("max_iter",1000)
                    , ("tol",1e-9)
#                    , ("Timeout", 1e6)
#                    , ("UserHM", True)
#                    , ("ScaleConIter",True)
#                    , ("ScaledFD",True)
#                    , ("ScaledKKT",True)
#                    , ("ScaledObj",True)
#                    , ("ScaledQP",True)
                    ]
    
    # initial conditions
#    ocp.guessX(x0)
#    for k in range(0,nk+1):
#        val = 2.0*pi*k/nk
#        ocp.guess('delta',val,timestep=k,quiet=True)
#
#    ocp.guess('aileron',0)
#    ocp.guess('elevator',0)
#    ocp.guess('tc',0)
    ocp.guess('endTime',5.4)
#
#    ocp.guess('ddr',0)
    ocp.guess('w0',10)

    print "setting up collocation..."
    ocp.setupCollocation(ocp.lookup('endTime'))
    print "setting up solver..."
    ocp.setupSolver( solverOpts=solverOptions,
                     callback=MyCallback() )
    return ocp


if __name__=='__main__':
    context   = zmq.Context(1)
    publisher = context.socket(zmq.PUB)
    publisher.bind("tcp://*:5563")

    print "reading config..."
    conf = readConfig('config.ini','configspec.ini')
    
    print "creating model..."
    dae = model.model(conf,extraParams=['endTime'])

    print "setting up ocp..."
    ocp = setupOcp(dae,conf,publisher,nk=50)

    # load initial guess
    f = open('crosswind_guess.txt','r')
    xutraj = []
    for line in f:
        xutraj.append([float(x) for x in line.strip('[]\n').split(',')])
    f.close()
    xutraj = numpy.array(xutraj)

    print "interpolating initial guess..."
    xuguess = numpy.array([numpy.interp(numpy.linspace(0,1,ocp.nk+1), numpy.linspace(0,1,xutraj.shape[0]), xutraj[:,k]) for k in range(xutraj.shape[1])])
    for k in range(ocp.nk+1):
        ocp.guessX(xuguess[:len(ocp.dae.xNames()),k],timestep=k,quiet=True)
        if k < ocp.nk:
            ocp.guessU(xuguess[len(ocp.dae.xNames()):,k],timestep=k,quiet=True)
    ocp.guess('delta',0,quiet=True)
    ocp.guess('ddelta',0,quiet=True)
    ocp.guess('winch_energy',0,quiet=True)
    

    xOpt = None
    for w0 in [10]:
        ocp.bound('w0',(w0,w0),force=True)
        opt = ocp.solve(xInit=xOpt)
        xup = opt['vardict']
        xOpt = opt['X_OPT']
        
        for k in range(0,ocp.nk):
            j = ocp.nicp*(ocp.deg+1)*k
            oldKites.append( kiteproto.toKiteProto(C.DMatrix(opt['x'][:,j]),C.DMatrix(opt['u'][:,j]),C.DMatrix(opt['p']), conf['kite']['zt'], conf['carousel']['rArm']) )

    print "optimal power: "+str(opt['vardict']['winch_energy'][-1]/opt['vardict']['endTime'])
    # Plot the results
    ocp.plot(['x','y','z'],opt)
    ocp.plot(['aileron','elevator'],opt,title='control surface inputs')
    ocp.plot(['tc'],opt,title='motor inputs (tc)')
    ocp.plot(['ddr'],opt,title='winch accel (ddr)')
    ocp.plot(['c','cdot'],opt,title="invariants")
    ocp.plot('airspeed',opt)
    ocp.plot(['alpha(deg)','beta(deg)','alphaTail(deg)','betaTail(deg)'],opt)
    ocp.plot('cL',opt)
    ocp.plot('cD',opt)
    ocp.plot('L/D',opt)
    ocp.plot('motor power',opt)
#    ocp.plot('winch power',opt)
    ocp.plot('winch_energy',opt)
    plt.show()