import math
from datetime import datetime, timezone
LAT, LON = 39.93999, -75.18101
def elevation(dt_utc):
    """NOAA-ish solar elevation in degrees."""
    n = dt_utc.timetuple().tm_yday
    h = dt_utc.hour + dt_utc.minute/60
    g = 2*math.pi/365*(n-1+(h-12)/24)
    decl = 0.006918-0.399912*math.cos(g)+0.070257*math.sin(g)-0.006758*math.cos(2*g)+0.000907*math.sin(2*g)-0.002697*math.cos(3*g)+0.00148*math.sin(3*g)
    eqt = 229.18*(0.000075+0.001868*math.cos(g)-0.032077*math.sin(g)-0.014615*math.cos(2*g)-0.040849*math.sin(2*g))
    tst = h*60 + eqt + 4*LON
    ha = math.radians(tst/4 - 180)
    lat = math.radians(LAT)
    cz = math.sin(lat)*math.sin(decl)+math.cos(lat)*math.cos(decl)*math.cos(ha)
    return math.degrees(math.asin(max(-1,min(1,cz))))
def haurwitz(elev):
    if elev <= 0: return 0.0
    s = math.sin(math.radians(elev)); return 1098*s*math.exp(-0.057/s)
