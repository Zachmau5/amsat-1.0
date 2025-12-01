"""
Coordinate conversion utilities for Keplerian orbital elements.

Purpose
-------
Implement the core transformations used by the Keplerian predictor:
  - Secular precession of RAAN and argument of perigee due to J2.
  - Conversion from Keplerian elements to Earth-Centered Inertial (ECI).
  - Conversion from ECI to Earth-Centered Earth-Fixed (ECEF).
  - Conversion from ECEF to geodetic longitude/latitude (Bowring).

Role in System
--------------
- Used by legacy Keplerian propagation workflows.
- Independent of the Skyfield-based tracking path.

High-level Flow (Pseudocode)
----------------------------
  1. RAANPrecession(a, e, i):
       - Apply standard J2 nodal precession formula.
  2. ArgPerigeePrecession(a, e, i):
       - Apply standard J2 perigee precession formula.
  3. ConvertKeplerToECI(a, e, i, Omega, w, nu, time_vec):
       a. Compute J2-induced changes to Omega and w.
       b. Precompute sin/cos of angles.
       c. Compute orbital radius r and PQW position.
       d. Form rotation matrix PQW -> ECI and apply to position.
       e. Compute velocity in PQW and rotate to ECI.
  4. ConvertECIToECEF(X_eci, Y_eci, Z_eci, gmst):
       - Apply rotation about Z by GMST.
  5. ComputeGeodeticLon(X_ecef, Y_ecef):
       - Use atan2(Y, X).
  6. ComputeGeodeticLat2(X_ecef, Y_ecef, Z_ecef, a, e):
       - Use Bowring’s method to compute latitude.
"""

import numpy as np
import constants as c


def RAANPrecession(a, e, i):
    """
    Secular precession of RAAN due to J2.

    Returns
    -------
    precession : float or ndarray
        RAAN precession rate in rad/s.
    """
    e_sq = e * e
    precession = np.divide(
        -1.5 * c.J2 * np.sqrt(c.GM) * (c.Re * c.Re) * np.cos(i),
        np.power(a, 3.5) * (1.0 - e_sq) * (1.0 - e_sq),
    )
    return precession


def ArgPerigeePrecession(a, e, i):
    """
    Secular precession of the argument of perigee due to J2.

    Returns
    -------
    precession : float or ndarray
        Argument of perigee precession rate in rad/s.
    """
    e_sq = e * e
    sin_i = np.sin(i)
    sin_i_sq = sin_i * sin_i
    precession = np.divide(
        0.75 * c.J2 * np.sqrt(c.GM) * (c.Re * c.Re) * (5.0 * sin_i_sq - 1.0),
        np.power(a, 3.5) * (1.0 - e_sq) * (1.0 - e_sq),
    )
    return precession


def ConvertKeplerToECI(a, e, i, Omega, w, nu, time_vec):
    """
    Convert Keplerian elements to ECI position and velocity.

    Parameters are arrays of the same shape.
    """
    # Precession updates (time_vec in days → seconds)
    w_precession = ArgPerigeePrecession(a, e, i)
    w = w + (time_vec * (24.0 * 3600.0)) * w_precession

    Omega_precession = RAANPrecession(a, e, i)
    Omega = Omega + (time_vec * (24.0 * 3600.0)) * Omega_precession

    sinnu = np.sin(nu)
    cosnu = np.cos(nu)
    sini = np.sin(i)
    cosi = np.cos(i)
    sinw = np.sin(w)
    cosw = np.cos(w)
    sinOmega = np.sin(Omega)
    cosOmega = np.cos(Omega)

    e_sq = e * e
    r = np.divide(a * (1.0 - e_sq), 1.0 + e * cosnu)
    x_pqw = r * cosnu
    y_pqw = r * sinnu

    R11 = cosw * cosOmega - sinw * cosi * sinOmega
    R12 = -(sinw * cosOmega + cosw * cosi * sinOmega)
    R21 = cosw * sinOmega + sinw * cosi * cosOmega
    R22 = -sinw * sinOmega + cosw * cosi * cosOmega
    R31 = sinw * sini
    R32 = cosw * sini

    X_eci = R11 * x_pqw + R12 * y_pqw
    Y_eci = R21 * x_pqw + R22 * y_pqw
    Z_eci = R31 * x_pqw + R32 * y_pqw

    coeff = np.sqrt(c.GM * a) / r
    sinE = (sinnu * np.sqrt(1.0 - e_sq)) / (1.0 + e * cosnu)
    cosE = (e + cosnu) / (1.0 + e * cosnu)
    local_vx = coeff * (-sinE)
    local_vy = coeff * (np.sqrt(1.0 - e_sq) * cosE)

    Xdot_eci = R11 * local_vx + R12 * local_vy
    Ydot_eci = R21 * local_vx + R22 * local_vy
    Zdot_eci = R31 * local_vx + R32 * local_vy

    return X_eci, Y_eci, Z_eci, Xdot_eci, Ydot_eci, Zdot_eci


def ConvertECIToECEF(X_eci, Y_eci, Z_eci, gmst):
    """Rotate ECI coordinates into ECEF using GMST (radians)."""
    X_ecef = X_eci * np.cos(gmst) + Y_eci * np.sin(gmst)
    Y_ecef = -X_eci * np.sin(gmst) + Y_eci * np.cos(gmst)
    Z_ecef = Z_eci
    return X_ecef, Y_ecef, Z_ecef


def ComputeGeodeticLon(X_ecef, Y_ecef):
    """Compute geodetic longitude from ECEF X/Y (radians)."""
    return np.arctan2(Y_ecef, X_ecef)


def ComputeGeodeticLat2(X_ecef, Y_ecef, Z_ecef, a, e):
    """
    Compute geodetic latitude from ECEF coordinates using Bowring’s method.
    """
    asq = a * a
    esq = e * e
    b = a * np.sqrt(1.0 - esq)
    bsq = b * b
    p = np.sqrt(X_ecef * X_ecef + Y_ecef * Y_ecef)
    ep = np.sqrt(asq - bsq) / b
    theta = np.arctan2(a * Z_ecef, b * p)
    sintheta = np.sin(theta)
    costheta = np.cos(theta)

    phi = np.arctan2(
        Z_ecef + ep * ep * b * sintheta ** 3,
        p - esq * a * costheta ** 3,
    )

    return phi
