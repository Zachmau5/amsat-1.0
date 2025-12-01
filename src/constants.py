"""
Physical and numerical constants used by the Keplerian predictor.

Purpose
-------
Centralize constants required for orbital mechanics and coordinate transforms:
  - Gravitational parameter GM.
  - J2 coefficient.
  - Earth radii.
  - Basic unit conversions.
  - Earth rotation rate.
#!/usr/bin/env python3
Role in System
--------------
- Imported by coordinate_conversions.py and other Keplerian utilities.
- All constants are expressed in SI units unless stated otherwise.
"""

import numpy as np

# Gravitational constant times Earth mass (m^3/s^2)
GM = 3.986004418e14

# Second zonal harmonic coefficient (dimensionless)
J2 = 1.0827e-3

# Earth equatorial radius (m)
Re = 6.378137e6

# Conversion factors
deg2rad = np.pi / 180.0
rad2deg = 180.0 / np.pi
twoPi = 2.0 * np.pi

# Days ↔ seconds
day2sec = 1.0 / (24.0 * 3600.0)

# Default number of time samples for propagation grids
num_time_pts = 1000

# Earth rotation rate: originally in deg/min, converted to rad/s.
omega_earth = 0.2506844773746215 * (deg2rad / 60.0)

# Default observer latitude (radians)
lat0 = 45.0 * deg2rad

earthEquatorialRadius = Re
earthPolarRadius = 6.356752e6
