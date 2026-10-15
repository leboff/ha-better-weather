"""Small, parameterized NOAA approximation matching the fixture reference."""
import math
from datetime import UTC


def elevation(instant, latitude, longitude):
    """Solar elevation in degrees at an aware instant and geographic coordinates."""
    dt = instant.astimezone(UTC)
    n = dt.timetuple().tm_yday
    h = dt.hour + dt.minute / 60
    g = 2 * math.pi / 365 * (n - 1 + (h - 12) / 24)
    decl = (0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g)
            - 0.006758 * math.cos(2*g) + 0.000907 * math.sin(2*g)
            - 0.002697 * math.cos(3*g) + 0.00148 * math.sin(3*g))
    eqt = 229.18 * (0.000075 + 0.001868 * math.cos(g) - 0.032077 * math.sin(g)
                    - 0.014615 * math.cos(2*g) - 0.040849 * math.sin(2*g))
    ha = math.radians((h * 60 + eqt + 4 * longitude) / 4 - 180)
    lat = math.radians(latitude)
    cz = math.sin(lat)*math.sin(decl) + math.cos(lat)*math.cos(decl)*math.cos(ha)
    return math.degrees(math.asin(max(-1, min(1, cz))))
