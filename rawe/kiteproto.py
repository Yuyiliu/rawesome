import kite_pb2

def toKiteProto(lookup,zt,rArm,w0=None,kiteAlpha=1.0,lineAlpha=1.0):
    cs = kite_pb2.CarouselState()

    cs.kiteXyz.x = lookup('x')
    cs.kiteXyz.y = lookup('y')
    cs.kiteXyz.z = lookup('z')

    cs.kiteDcm.r11 = lookup('e11')
    cs.kiteDcm.r12 = lookup('e12')
    cs.kiteDcm.r13 = lookup('e13')

    cs.kiteDcm.r21 = lookup('e21')
    cs.kiteDcm.r22 = lookup('e22')
    cs.kiteDcm.r23 = lookup('e23')

    cs.kiteDcm.r31 = lookup('e31')
    cs.kiteDcm.r32 = lookup('e32')
    cs.kiteDcm.r33 = lookup('e33')

    try:
        cs.delta = lookup('delta')
    except NameError:
        cs.delta = 0

    cs.rArm = rArm
    cs.zt = zt

    if w0 is not None:
        cs.w0 = w0
        
    cs.kiteTransparency = kiteAlpha
    cs.lineTransparency = lineAlpha

    return cs
