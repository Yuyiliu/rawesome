import numpy as np

import casadi as C

class VectorizedReadOnlyNmheMap(object):
    """
    Initialize this with a vector (like MX or numpy.array)
    and it will provide efficient slices with xVec/uVec/pVec.
    It will also provide lookup(name,timestep) functionality
    """
    def __init__(self,dae,nk,vec):
        self._nk = nk
        self._xNames = dae.xNames()
        self._pNames = dae.pNames()
        self._vec = vec

        xSize = len(self._xNames)
        pSize = len(self._pNames)
        mapSize = xSize*(self._nk+1) + pSize
        if type(self._vec) in [C.MX,C.SXMatrix]:
            assert (mapSize == self._vec.size()), "vector size is wrong"
        elif type(self._vec) in [np.array,np.ndarray]:
            assert (mapSize == self._vec.size), "vector size is wrong"
        else:
            raise ValueError("unrecognized type: "+str(type(self._vec)))
        
        # set up xVec,pVec
        vecIdx = 0
        self._p = self._vec[vecIdx:vecIdx+pSize]
        vecIdx += pSize

        self._X = []
        self._U = []
        for ts in range(self._nk):
            self._X.append(self._vec[vecIdx:vecIdx+xSize])
            vecIdx += xSize
        self._X.append(self._vec[vecIdx:vecIdx+xSize])
        vecIdx += xSize
        assert (vecIdx == mapSize)

        # set up indexes
        self._xIdx = {}
        self._pIdx = {}
        for k,name in enumerate(self._xNames):
            self._xIdx[name] = k
        for k,name in enumerate(self._pNames):
            self._pIdx[name] = k

    def vectorize(self):
        return self._vec
    
    def xVec(self,timestep):
        assert (timestep != None), "please set timestep"
        assert (timestep <= self._nk), "timestep too large"
        return self._X[timestep]
    def pVec(self):
        return self._p
    
    def lookup(self,name,timestep=None):
        if name in self._xIdx:
            return self.xVec(timestep)[self._xIdx[name]]
        elif name in self._pIdx:
            assert (timestep == None), "don't set timestep for parameter"
            return self.pVec()[self._pIdx[name]]
        else:
            raise NameError('unrecognized name "'+name+'"')
    

class WriteableNmheMap(object):
    """
    Initialize this with a dae and number of control intervals and
    it will set all elements to None. Then you can call setVal() to set them
    and lookup() or vectorize() to retrieve them.
    You can also call getMissing() to get a summary of elements which haven't been set
    """
    def __init__(self,dae,nk):
        self._nk = nk
        self._xNames = dae.xNames()
        self._pNames = dae.pNames()

        self._X = np.resize(np.array([None]),(self._nk+1,dae.xVec().size()))
        self._p = np.resize(np.array([None]),dae.pVec().size())
        
        self._xIdx = {}
        self._pIdx = {}
        for k,name in enumerate(self._xNames):
            self._xIdx[name] = k
        for k,name in enumerate(self._pNames):
            self._pIdx[name] = k

    def vectorize(self):
        return np.concatenate([self.pVec()]+[self.xVec(k) for k in range(self._nk+1)])
    
    def xVec(self,timestep):
        assert (timestep != None), "please set timestep"
        assert (timestep <= self._nk), "timestep too large"
        return self._X[timestep,:]
    def pVec(self):
        return self._p
    
    def lookup(self,name,timestep=None):
        if name in self._xIdx:
            assert (timestep != None), "please set timestep"
            assert (timestep <= self._nk), "timestep too large"
            return self._X[timestep][self._xIdx[name]]
        elif name in self._pIdx:
            assert (timestep == None), "don't set timestep for parameter"
            return self._p[self._pIdx[name]]
        else:
            raise NameError('unrecognized name "'+name+'"')

    def setVal(self,name,val,timestep=None):
        if name in self._xIdx:
            if timestep == None:
                for k in range(self._nk+1):
                    self.setVal(name,val,timestep=k)
                return
            assert (timestep <= self._nk), "timestep too large"
            self._X[timestep,self._xIdx[name]] = val
        elif name in self._pIdx:
            assert (timestep == None), "don't set timestep for parameter"
            self._p[self._pIdx[name]] = val
        else:
            raise NameError('unrecognized name "'+name+'"')

    def getMissing(self):
        xuMissing = {}
        for name in self._xNames:
            missing = []
            for k in range(self._nk+1):
                if self.lookup(name,timestep=k) is None:
                    missing.append(k)
            if len(missing)>0:
                xuMissing[name] = missing
        pMissing = []
        for name in self._pNames:
            if self.lookup(name) is None:
                pMissing.append(name)
        return (xuMissing,pMissing)


class NmheOutputMapGenerator(object):
    """
    Something which will efficiently generate a map of all outputs.
    The outputs are all computed all at once to ensure no (additional) CSEs are generated.

    On initialization, the function which creates all the outputs from a dv vector is created.
    Then you use it to initialize an OutputMap object
    """
    def __init__(self,ocp,U):
        (fAll,(f0,outputNames0)) = ocp.dae.outputsFun()
        self._outputNames0 = outputNames0
        self._outputNames = ocp.dae.outputNames()

        assert (len(self._outputNames0) == f0.getNumOutputs())
        assert (len(self._outputNames) == fAll.getNumOutputs())

        self._nk = ocp.nk

        outs = []
        for timestepIdx in range(self._nk):
            if f0 is not None:
                outs += f0.call([ocp._dvMap.xVec(timestepIdx),
                                 U[timestepIdx,:].T,
                                 ocp._dvMap.pVec()])
        # make the function
        self.fEveryOutput = C.MXFunction([ocp._dvMap.vectorize(),U],outs)
        self.fEveryOutput.init()


class NmheOutputMap(object):
    """
    Initialize this with an outputMapGenerator, a vector of design vars, and a matrix of control inputs.
    If you pass a symbolic vector you get symbolic outputs with MXFunction.call().
    If you pass a numeric vector you get numeric outputs with MXFunction.setInput(); MXFunction.evaluate(); ..
    """
    def __init__(self,outputMapGenerator,dvs,U):
        if type(dvs) == C.MX:
            allOutputs = outputMapGenerator.fEveryOutput.call([dvs,U])
        elif type(dvs) == C.SXMatrix:
            allOutputs = outputMapGenerator.fEveryOutput.eval([dvs,U])
        elif type(dvs) in [np.ndarray,C.DMatrix]:
            outputMapGenerator.fEveryOutput.setInput(dvs,0)
            outputMapGenerator.fEveryOutput.setInput(U,1)
            outputMapGenerator.fEveryOutput.evaluate()
            allOutputs = [np.array(outputMapGenerator.fEveryOutput.output(k)).squeeze()
                          for k in range(outputMapGenerator.fEveryOutput.getNumOutputs())]
        else:
            raise TypeError("OutputMap got unrecognized design vector type: "+str(type(dvs)))

        self._outputNames0 = outputMapGenerator._outputNames0
        self._outputNames = outputMapGenerator._outputNames

        self._numOutputs0 = len(self._outputNames0)
        self._numOutputs  = len(self._outputNames)

        self._nk = outputMapGenerator._nk

        self._outputs0 = {}

        for name in self._outputNames0:
            self._outputs0[name] = np.resize(np.array([None]),self._nk)

        outs = []
        k = 0
        for timestepIdx in range(self._nk):
            # outputs defined at tau_i0
            outs = allOutputs[k:k+self._numOutputs0]
            k += self._numOutputs0
            for name,val in zip(self._outputNames0,outs):
                self._outputs0[name][timestepIdx] = val

    def lookup(self,name,timestep):
        if name not in self._outputNames:
            raise NameError("couldn't find \""+name+"\"")

        if name not in self._outputs0:
            raise ValueError("sorry, \""+name+"\" depends on algebraic variable or ddt(differential variable) \
                             and Multiple Shooting cannot access it")

        assert (timestep != None), "please set timestep"
        return self._outputs0[name][timestep]
