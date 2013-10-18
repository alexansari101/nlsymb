import numpy as np
from scipy.linalg import schur
from numpy.linalg import inv
from scipy.integrate import ode
from scipy.integrate import trapz

from . import matmult, sysIntegrate, Trajectory


class LQR:
    # a class representing a LQR problem
    def __init__(self, A, B, Q=None, R=None, tlims=(0, 10),
                 Rscale=1, Qscale=1, PT=None):
        attribs = {'Q': Q, 'R': R, 'PT': PT, 'A': A, 
                   'B': B, 'tlims': tlims}
        for k, v in attribs.iteritems():
            setattr(self, k, v)

        self.t0 = tlims[0]
        self.tf = tlims[1]

        Adim = np.array(A(tlims[0])).shape
        if Adim[0] != Adim[1]:
            raise NameError('A must be square')
        else:
            self.n = Adim[0]

        Bdim = np.array(B(tlims[0])).shape
        if Bdim[0] != self.n:
            raise NameError("B's first dimension must match A")
        else:
            self.m = Bdim[1]

        if Q is None:
            # define it as identity
            self.Q = lambda t: Qscale*np.eye(self.n)

        if R is None:
            self.R = lambda t: Rscale*np.eye(self.m)

        if PT is None:
            #self.PT = np.eye(self.n)
            care = CARE(self.A(tb), self.B(tb), 
                        R=self.R(tb), Q=self.Q(tb))
            self.PT = care.P

        # this is a Trajectory object
        self.Ptraj = self.cdre()
        self.P = self.Ptraj.P

        self.Ktraj = Trajectory('K')
        for (t, P) in zip(self.Ptraj._t, self.Ptraj.P.y):
            K = matmult(inv(self.R(t)), self.B(t).T, P)
            self.Ktraj.addpoint(t, K=K)
        self.Ktraj.interpolate()
        self.K = self.Ktraj.K

# check that the dimensions of A and B are correct and return them
def DimExtract(A, B):
    AA, BB = map(np.array, (A, B))

    if AA.shape[0] is not AA.shape[1]:
        raise Exception("A needs to be square")
    
    if AA.shape[0] is not BB.shape[0]:
        raise Exception("A and B need to share the first dimension")

    return BB.shape

# continuous algebraic ricatti equation and solver
class CARE(object):
    def __init__(self, A, B, **kwargs):
        self.dims = DimExtract(A, B)
        self.n, self.m = self.dims

        self.A = np.array(A)
        self.B = np.array(B)

        # get R and Q from qwargs 
        keys = kwargs.keys()
        self.Q = np.array(kwargs['Q']) if 'Q' in keys \
            else np.eye(self.n)
        self.R = np.array(kwargs['R']) if 'R' in keys \
            else np.eye(self.n)
        
    def _solve(self):
        BRB = matmult(self.B, inv(self.R), self.B.T)
        M = np.vstack([
            np.hstack([self.A, -BRB]),
            np.hstack([-self.Q, -self.A.T])
        ])
        (L, Z, sdim) = schur(M, sort='lhp')
        U = Z.T

        self._P = matmult(inv(U[0:sdim, 0:sdim]),
                    U[0:sdim, sdim:]).conj().T

        return self._P
    
    @property
    def P(self):
        if hasattr(self, '_P'):
            return self._P
        else:
            return self.solve()

# continuous differential ricatti equation and solver
class CDRE(object):
    def __init__(self, tlims, A, B, **kwargs):
        self.tlims = tlims
        self.ta, self.tb = self.tlims

        self.dims = DimExtract(A(ta), B(ta))
        n, m = self.dims

        self.A, self.B = A, B

        # get R and Q and Pb from qwargs 
        self.Q = kwargs['Q'] if 'Q' in kwargs.keys() \
            else lambda t : np.eye(n)
        self.R = kwargs['R'] if 'R' in kwargs.keys() \
            else lambda t : np.eye(m)
 
        # if Pb is not given get it from solving
        # a CARE at the final time
        if 'Pb' in kwargs.keys():
            self.Pb = kwargs['Pb']
        else:
            care = CARE(self.A(tb), self.B(tb), 
                        R=self.R(tb), Q=self.Q(tb))
            self.Pb = care.P
        
        self.Pdot = lambda s, P: self._Pdot(s, P)

    def _Pdot(self, s, P):
        A, B = self.A(-s), self.B(-s)
        R, Q = self.R(-s), self.Q(-s)

        # rebuild the matrix from the array
        P = P.reshape((self.n, self.n))
        # do necessary matrix algebra
        Pd = matmult(P, B, inv(R), B.T, P) \
                - matmult(A.T, P) - matmult(P, A) - Q
        # ravel and multiply by -1 (for backwards integration)
        return -Pd.ravel()
        
    def _solve(self, **kwargs):
        sa, sb = -(self.ta,self.tb)
        solver = ode(self.Pdot)
        solver.set_integrator('vode', **kwargs)
        solver.set_initial_value(self.Pb.ravel(), sb)
        
        self._Pt = Trajectory('P')
        self._Pt.addpoint(-sb, P=Pb)

        while solver.successful() and solver.t < sa:
            solver.integrate(sa, step=True)
            self.Ptraj.addpoint(-solver.t, solver.y.reshape((n,n)))
    
        self._Pt.interpolate()
        return self._Pt

    def P(self, t):
        if hasattr(self, '_Pt'):
            return self._Pt.P(t)
        else:
            #                 ,here we add max_step option
            return self.solve().P(t)

class Controller():
    def __init__(self, **kwargs):
        self.ref = kwargs['reference']
        self.n = len(self.ref._x[0])
        self.m = len(self.ref._u[0])
        self.C = kwargs['C'] if 'C' in kwargs.keys() else np.zeros(m)
        if 'K' in kwargs.keys():
            self.K = kwargs['K']
        else:
            self.K = np.zeros((m,n))

    def __call__(self, t, x):
        return self.ref.u(t) - \
                matmult(self.K(t), x - self.ref.x(t)) - self.C(t)

class DescentDir(LQR):
    def __init__(self, traj, ref, cost=None, **kwargs):
        # not passing R and Q to LQR, getting set to
        # identity by default
        LQR.__init__(self, traj.A, traj.B, **kwargs)

        self._cost = cost
        self.traj = traj
        self.ref = ref
        tf = self.tf
        P1 = cost.PT
        self.rf = np.dot(P1, self.traj.x(tf) - self.ref.x(tf))
        self.a = lambda t: np.dot(cost.Q(t), self.traj.x(t) - self.ref.x(t))
        self.b = lambda t: np.dot(cost.R(t), self.traj.u(t) - self.ref.u(t))

        self.r = self.rsolve()

        if 'z0' in kwargs.keys():
            self.z0 = kwargs['z0']
        else:
            #self.z0 = -np.dot(inv(self.P(0)), self.r(0))
            self.z0 = np.zeros(self.n)

        self.direction = self.solve(self.z0)

        self.z = self.direction.z
        self.v = self.direction.v
        self._z = self.direction._z
        self._v = self.direction._v
        self._t = self.direction._t
        self.tmin = self.direction.tmin
        self.tmax = self.direction.tmax

    def rsolve(self):
        def rdot(s, r):
            t = -s
            A = self.A(t)
            B = self.B(t)
            Rinv = inv(self.R(t))
            P = self.P(t)
            b = self.b(t)
            a = self.a(t)
            out = np.dot((A - matmult(B, Rinv, B.T, P)).T, r)
            out += a - matmult(P, B, Rinv, b)
            # two minus signs make a plus below (one from equation
            # and one from backwards integration)
            return out

        # leaving this here until I change sysIntegrate to handle
        # backwards integration
        solver = ode(rdot)
        solver.set_integrator('vode', max_step=1e-1, min_step=1e-13)
        solver.set_initial_value(self.rf, -self.tf)
        t = [-self.tf]
        r = [self.rf]

        while solver.successful() and solver.t < self.t0:
            solver.integrate(self.t0, step=True)
            r.append(solver.y)
            t.append(-solver.t)

        rtraj = Trajectory('r')
        for tt, rr in zip(t, r):
            rtraj.addpoint(tt, r=rr)
        rtraj.interpolate()

        return rtraj.r

    def solve(self, z0):
        def v(t, z):
            Ri = inv(self.R(t))
            b = self.b(t)
            P = self.P(t)
            r = self.r(t)
            B = self.B(t)
            return -np.dot(Ri, b + matmult(B.T, P, z) + np.dot(B.T, r))

        def zdot(t, z):
            return np.dot(self.A(t), z) + np.dot(self.B(t), v(t, z))

        (t, z) = sysIntegrate(zdot, z0, tlimits=self.tlims)
        lintraj = Trajectory('z', 'v')
        for (tt, zz) in zip(t, z):
            lintraj.addpoint(tt, z=zz, v=v(tt, zz))
        lintraj.interpolate()

        return lintraj

    @property
    def cost(self):
        elist = []
        tlist = self._t
        T = self.tmax
        for (t, z, v) in zip(tlist, self._z, self._v):
            expr = matmult(z, self._cost.Q(t), z) + matmult(v, self._cost.R(t), v)
            elist.append(expr)

        out = trapz(elist, tlist) + matmult(self.z(T), self._cost.PT, self.z(T))

        return out

    def __rmul__(self, scalar):
        # should only be called for a descent direction
        # which only has z and v components
        out = Trajectory('z', 'v')
        for (t, z, v) in zip(self._t, self._z, self._v):
            out.addpoint(t, z=scalar*z, v=scalar*v)

        out.interpolate()
        return out

# redefine LQR class to behave better (and more generally)
class LQR(CDRE):
    """
    what we need:
     tlims = (ta, tb) : time interval
     A(t), B(t) : linear system matrices
     xa : initial condition
     dims : optional, dimensions of state and control
     Q(t), S(t), R(t), Qf=Pb : cost function matrices
                            if not provided, default is identity
     NOTE: S(t) not actually implemented
    """
    def __init__(self, tlims, A, B, xa, **kwargs):
        CDRE.__init__(tlims, A, B, **kwargs)
        
        self.xa = xa
        self.Qf = self.Pb
        if 'S' in kwargs.keys():
            self.S = kwargs['S']
        else:
            self.S = lambda t : np.zeros((n,m))

    def _solve(self):
        CDRE._solve()
        self._Kt = Trajectory('K')
        for (t, P) in zip(self._Pt._t, self._Pt._P):
            K = matmult(inv(self.R(t)), self.B(t).T, P)
            self._Kt.addpoint(t, K=K)

        self._Kt.interpolate()
        return self._Kt

    def K(self,t):
        if hasattr(self, '_Kt'):
            return self._Kt.K(t)
        else:
            return self._solve().K(t)

        

# class that implements an LQ problem and solver
class LQ(object):
    """
    what we need:
     dims : optional, dimensions of state and control
     tlims = (ta, tb) : time interval
     A(t), B(t) : linear system matrices
     q(t), r(t), qf : linear terms in cost function
     Q(t), S(t), R(t), Qf : quadratic model matrices
     xa : initial condition
     tswitch : optional, list of times at which dynamics switch
     fswitch : list of jump terms to be added to q(t) at tswitch
    """
    def __init__(self, tlims, A, B, xa, **kwargs):
        self.ta = tlims(0)
        self.tb = tlims(1)
        self.A = A
        self.B = B
        self.xa = xa
        # TODO take care of all the other initializations

        


