def calculate_resistances(V_j2, V_j3, V, V_i2, V_i3, R_i, R_j):
    """
    Calculate R_2G, R_3G, and R_23 from the measured voltages
    and known gate resistances.

    Parameters
    ----------
    V_j2 : float
        Voltage V_j2.
    V_j3 : float
        Voltage V_j3.
    V : float
        Applied voltage V.
    V_i2 : float
        Voltage V_i2.
    V_i3 : float
        Voltage V_i3.
    R_i : float
        Known resistance R_i.
    R_j : float
        Known resistance R_j.

    Returns
    -------
    R_2G : float
        Resistance from conductor 2 to ground.
    R_3G : float
        Resistance from conductor 3 to ground.
    R_23 : float
        Resistance between conductors 2 and 3.
    """

    R_2G = (
        V_j2 * V_j3
        - (V - V_i2) * (V - V_i3)
    ) / (
        (V_i3 / R_i - V_j2 / R_j) * V_j3
        - (V_i2 / R_i - V_i3 / R_j) * (V - V_i3)
    )

    R_3G = V_j3 / (
        (V_i2 / R_i - V_j3 / R_j)
        - (V - V_i2) / R_2G
    )

    R_23 = (
        (V - V_i2) - V_j2
    ) / (
        V_i2 / R_i - (V - V_i2) / R_2G
    )

    return R_2G, R_3G, R_23


# Example values
V_j2 = 9.8
V_j3 = 9.8
V = 50
V_i2 = 16
V_i3 = 11
R_i = 100
R_j = 100

R_2G, R_3G, R_23 = calculate_resistances(
    V_j2, V_j3, V, V_i2, V_i3, R_i, R_j
)

print(f"R_2G = {R_2G:.1f} ohms")
print(f"R_3G = {R_3G:.1f} ohms")
print(f"R_23 = {R_23:.1f} ohms")
