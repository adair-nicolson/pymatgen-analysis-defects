"""Defect corrections module."""

from __future__ import annotations

import logging
from typing import Optional

import matplotlib.pyplot as plt
import numpy as np
import scipy
from numpy.typing import ArrayLike
from pymatgen.io.vasp.outputs import Locpot
from scipy import stats

from pymatgen.analysis.defects.utils import (
    QModel,
    ang_to_bohr,
    converge,
    eV_to_k,
    generate_reciprocal_vectors_squared,
    hart_to_ev,
)

__author__ = "Jimmy-Xuan Shen, Danny Broberg, Shyam Dwaraknath"
__copyright__ = "Copyright 2022, The Materials Project"
__maintainer__ = "Jimmy-Xuan Shen"
__email__ = "jmmshn@gmail.com"

_logger = logging.getLogger(__name__)


"""
Adapted from the original code by Danny and Shyam.
Rewritten to be functional instead of object oriented.
"""


def get_freysoldt_correction(
    q: int,
    dielectric: float,
    defect_locpot: Locpot,
    bulk_locpot: Locpot,
    defect_frac_coords: Optional[ArrayLike] = None,
    energy_cutoff: float = 520,
    mad_tol: float = 1e-4,
    q_model: Optional[QModel] = None,
    step: float = 1e-4,
):
    """Gets the Freysoldt correction for a defect entry.

    Get the Freysoldt correction for a defect. The result is given
    as a dictionary with the following keys:
    - "freysoldt_electrostatic": the electrostatic portion of the correction
    - "freysoldt_potential_alignment": the potential alignment portion of the correction

    The second return value is a dictionary used to plot the planar average
    electrostatic potential (used for debugging).

    Args:
        q:
            Charge state of defect
        dielectric:
            Dielectric constant of bulk
        defect_locpot:
            Locpot of defect
        bulk_locpot:
            Locpot of bulk
        defect_frac_coords:
            Fractional coordinates of the defect.
        energy_cutoff:
            Maximum energy in eV in reciprocal space to perform integration
        mad_tol:
            Convergence criteria for the Madelung energy for potential correction
        q_model:
            QModel object to use for Freysoldt correction. If None, then uses default
        step:
            Step size for numerical integration.

    Returns:
        dict: Freysoldt correction values as a dictionary
        dict: Plot data for planar average electrostatic potential.
    """
    # dielectric has to be a float
    if isinstance(dielectric, (int, float)):
        dielectric = float(dielectric)
    else:
        dielectric = float(np.mean(np.diag(dielectric)))

    q_model = QModel() if q_model is None else q_model

    list_axis_grid = [*map(defect_locpot.get_axis_grid, [0, 1, 2])]
    list_defect_plnr_avg_esp = [*map(defect_locpot.get_average_along_axis, [0, 1, 2])]
    list_bulk_plnr_avg_esp = [*map(bulk_locpot.get_average_along_axis, [0, 1, 2])]
    list_axes = range(len(list_axis_grid))

    lattice = defect_locpot.structure.lattice.copy()

    es_corr = perform_es_corr(
        lattice=lattice,
        q=q,
        dielectric=dielectric,
        q_model=q_model,
        energy_cutoff=energy_cutoff,
        mad_tol=mad_tol,
        step=step,
    )

    pot_corrs = dict()
    plot_data = dict()

    for x, pureavg, defavg, axis in zip(
        list_axis_grid, list_bulk_plnr_avg_esp, list_defect_plnr_avg_esp, list_axes
    ):
        tmp_pot_corr, md = perform_pot_corr(
            axis_grid=x,
            pureavg=pureavg,
            defavg=defavg,
            lattice=lattice,
            q=q,
            defect_frac_coords=defect_frac_coords,
            axis=axis,
            dielectric=dielectric,
            q_model=q_model,
            mad_tol=mad_tol,
            widthsample=1.0,
        )
        pot_corrs[axis] = tmp_pot_corr
        plot_data[axis] = md

    # defect_entry.parameters["freysoldt_meta"] = dict(self.metadata)
    # defect_entry.parameters["potalign"] = pot_corr / (-q) if q else 0.0
    pot_corr = np.mean(list(pot_corrs.values()))
    frey_corr = {
        "freysoldt_electrostatic": es_corr,
        "freysoldt_potential_alignment": pot_corr,
    }
    return frey_corr, plot_data


def perform_es_corr(
    lattice, q, dielectric, q_model, energy_cutoff=520, mad_tol=1e-4, step=1e-4
) -> float:
    """Perform Electrostatic Freysoldt Correction.

    Perform the electrostatic Freysoldt correction for a defect.

    Args:
        lattice: Pymatgen lattice object
        q: Charge of defect
        dielectric: Dielectric constant of bulk
        q_model: QModel object to use for Freysoldt correction. If None, uses default
        energy_cutoff: Maximum energy in eV in reciprocal space to perform integration
        mad_tol: Convergence criteria for the Madelung energy for potential correction
        step: Step size for numerical integration

    Return:
        float:
            Electrostatic Point Charge contribution to Freysoldt Correction (float)
    """
    _logger.info(
        "Running Freysoldt 2011 PC calculation (should be equivalent to sxdefectalign)"
    )
    _logger.debug("defect lattice constants are (in angstroms)" + str(lattice.abc))

    [a1, a2, a3] = ang_to_bohr * np.array(lattice.get_cartesian_coords(1))
    logging.debug("In atomic units, lat consts are (in bohr):" + str([a1, a2, a3]))
    vol = np.dot(a1, np.cross(a2, a3))  # vol in bohr^3

    def e_iso(encut):
        gcut = eV_to_k(encut)  # gcut is in units of 1/A
        return (
            scipy.integrate.quad(lambda g: q_model.rho_rec(g * g) ** 2, step, gcut)[0]
            * (q**2)
            / np.pi
        )

    def e_per(encut):
        eper = 0
        for g2 in generate_reciprocal_vectors_squared(a1, a2, a3, encut):
            eper += (q_model.rho_rec(g2) ** 2) / g2
        eper *= (q**2) * 2 * round(np.pi, 6) / vol
        eper += (q**2) * 4 * round(np.pi, 6) * q_model.rho_rec_limit0 / vol
        return eper

    eiso = converge(e_iso, 5, mad_tol, energy_cutoff)
    _logger.debug("Eisolated : %f", round(eiso, 5))

    eper = converge(e_per, 5, mad_tol, energy_cutoff)

    _logger.info("Eperiodic : %f hartree", round(eper, 5))
    _logger.info("difference (periodic-iso) is %f hartree", round(eper - eiso, 6))
    _logger.info("difference in (eV) is %f", round((eper - eiso) * hart_to_ev, 4))

    es_corr = round((eiso - eper) / dielectric * hart_to_ev, 6)
    _logger.info("Defect Correction without alignment %f (eV): ", es_corr)
    return es_corr


def perform_pot_corr(
    axis_grid,
    pureavg,
    defavg,
    lattice,
    q,
    defect_frac_coords,
    axis,
    dielectric,
    q_model,
    mad_tol=1e-4,
    widthsample=1.0,
):
    """For performing planar averaging potential alignment.

    Args:
        axis_grid (1 x NGX where NGX is the length of the NGX grid
            in the axis direction. Same length as pureavg list):
                A numpy array which contain the Cartesian axis
                values (in angstroms) that correspond to each planar avg
                potential supplied.
        pureavg (1 x NGX where NGX is the length of the NGX grid in
            the axis direction.):
                A numpy array for the planar averaged
                electrostatic potential of the bulk supercell.
        defavg (1 x NGX where NGX is the length of the NGX grid in
            the axis direction.):
            A numpy array for the planar averaged
            electrostatic potential of the defect supercell.
        lattice: Pymatgen Lattice object of the defect supercell
        q (float or int): charge of the defect
        defect_frac_position: Fracitional Coordinates of the defect in the supercell
        axis (int): axis for performing the freysoldt correction on
        widthsample (float): width (in Angstroms) of the region in between defects
        where the potential alignment correction is averaged. Default is 1 Angstrom.

    Returns:
        Potential Alignment contribution to Freysoldt Correction (float)
    """
    logging.debug("run Freysoldt potential alignment method for axis " + str(axis))
    nx = len(axis_grid)

    # shift these planar averages to have defect at origin
    axfracval = defect_frac_coords[axis]
    axbulkval = axfracval * lattice.abc[axis]
    if axbulkval < 0:
        axbulkval += lattice.abc[axis]
    elif axbulkval > lattice.abc[axis]:
        axbulkval -= lattice.abc[axis]

    if axbulkval:
        for i in range(nx):
            if axbulkval < axis_grid[i]:
                break
        rollind = len(axis_grid) - i
        pureavg = np.roll(pureavg, rollind)
        defavg = np.roll(defavg, rollind)

    # if not self._silence:
    _logger.debug("calculating lr part along planar avg axis")
    reci_latt = lattice.reciprocal_lattice
    dg = reci_latt.abc[axis]
    dg /= ang_to_bohr  # convert to bohr to do calculation in atomic units

    # Build background charge potential with defect at origin
    v_G = np.empty(len(axis_grid), np.dtype("c16"))
    v_G[0] = 4 * np.pi * -q / dielectric * q_model.rho_rec_limit0
    g = np.roll(np.arange(-nx / 2, nx / 2, 1, dtype=int), int(nx / 2)) * dg
    g2 = np.multiply(g, g)[1:]
    v_G[1:] = 4 * np.pi / (dielectric * g2) * -q * q_model.rho_rec(g2)
    v_G[nx // 2] = 0 if not (nx % 2) else v_G[nx // 2]

    # Get the real space potential via fft and grabbing the imaginary portion
    v_R = np.fft.fft(v_G)

    if abs(np.imag(v_R).max()) > mad_tol:
        raise Exception("imaginary part found to be %s", repr(np.imag(v_R).max()))
    v_R /= lattice.volume * ang_to_bohr**3
    v_R = np.real(v_R) * hart_to_ev

    # get correction
    short = np.array(defavg) - np.array(pureavg) - np.array(v_R)
    checkdis = int((widthsample / 2) / (axis_grid[1] - axis_grid[0]))
    mid = int(len(short) / 2)

    tmppot = [short[i] for i in range(mid - checkdis, mid + checkdis + 1)]
    _logger.debug("shifted defect position on axis (%s) to origin", repr(axbulkval))
    _logger.debug(
        "means sampling region is (%f,%f)",
        axis_grid[mid - checkdis],
        axis_grid[mid + checkdis],
    )

    C = -np.mean(tmppot)
    _logger.debug("C = %f", C)
    final_shift = [short[j] + C for j in range(len(v_R))]
    v_R = [elmnt - C for elmnt in v_R]

    _logger.info("C value is averaged to be %f eV ", C)
    _logger.info(
        "Potentital alignment energy correction (-q*delta V):  %f (eV)", -q * C
    )
    pot_corr = -q * C

    # log plotting data:
    metadata = dict()
    metadata["pot_plot_data"] = {
        "Vr": v_R,
        "x": axis_grid,
        "dft_diff": np.array(defavg) - np.array(pureavg),
        "final_shift": final_shift,
        "check": [mid - checkdis, mid + checkdis + 1],
    }

    # log uncertainty:
    metadata["pot_corr_uncertainty_md"] = {
        "stats": stats.describe(tmppot)._asdict(),
        "potcorr": -q * C,
    }

    return pot_corr, metadata


def plot_plnr_avg(plot_data, title=None, saved=False):
    """Plot the planar average electrostatic potential.

    Plot the planar average electrostatic potential against the Long range and
    short range models from Freysoldt. Must run perform_pot_corr or get_correction
    (to load metadata) before this can be used.

    Args:
        plot_data (dict): Dictionary of FreysoldtCorrection metadata.
        title (str): Title to be given to plot. Default is no title.
        saved (bool): Whether to save file or not. If False then returns plot object.
        If True then saves plot as   str(title) + "FreyplnravgPlot.pdf"
    """
    if not plot_data["pot_plot_data"]:
        raise ValueError("Cannot plot potential alignment before running correction!")

    x = plot_data["pot_plot_data"]["x"]
    v_R = plot_data["pot_plot_data"]["Vr"]
    dft_diff = plot_data["pot_plot_data"]["dft_diff"]
    final_shift = plot_data["pot_plot_data"]["final_shift"]
    check = plot_data["pot_plot_data"]["check"]

    plt.figure()
    plt.clf()
    plt.plot(x, v_R, c="green", zorder=1, label="long range from model")
    plt.plot(x, dft_diff, c="red", label="DFT locpot diff")
    plt.plot(x, final_shift, c="blue", label="short range (aligned)")

    tmpx = [x[i] for i in range(check[0], check[1])]
    plt.fill_between(
        tmpx, -100, 100, facecolor="red", alpha=0.15, label="sampling region"
    )

    plt.xlim(round(x[0]), round(x[-1]))
    ymin = min(min(v_R), min(dft_diff), min(final_shift))
    ymax = max(max(v_R), max(dft_diff), max(final_shift))
    plt.ylim(-0.2 + ymin, 0.2 + ymax)
    plt.xlabel(r"distance along axis ($\AA$)", fontsize=15)
    plt.ylabel("Potential (V)", fontsize=15)
    plt.legend(loc=9)
    plt.axhline(y=0, linewidth=0.2, color="black")
    plt.title(str(title) + " defect potential", fontsize=18)
    plt.xlim(0, max(x))
    if saved:
        plt.savefig(str(title) + "FreyplnravgPlot.pdf")
        return None
    return plt


# TODO Currently requires using the implementation in sxdefectalign2d
# TODO Should be done natively since the IO of writing the potential
# is a bottleneck at production,.
def get_freysoldt2d_correction(
    q: int,
    dielectric: float,
    defect_locpot: Locpot,
    bulk_locpot: Locpot,
    defect_frac_coords: Optional[ArrayLike] = None,
    energy_cutoff: float = 520,
    dielectric: float,
    slab_buffer=2,
):
    sxdefectalign2d = which("sxdefectalign2d") or which("sxdefectalign2d.exe")
    if not sxdefectalign2d:
        raise RuntimeError("sxdefectalign2d is not in PATH")

    if float(q) == 0.0:
        return {'2d_electrostatic': 0.0}, {}
        
    slab_bottom = np.argmin(bulk_locpot.structure.cart_coords[:, 2])
    slab_bottom = bulk_locpot.structure[slab_bottom].coords[2]
    slab_top = np.argmax(bulk_locpot.structure.cart_coords[:, 2])
    slab_top = bulk_locpot.structure[slab_top].coords[2]
    defect_cart_coords = structure.lattice.get_cartesian_coords(defect_frac_coords)

    write_sphinx_input(
        dielectric_const=dielectric,
        lattice_matrix=bulk_locpot.structure.lattice.matrix,
        defect_sc_coords=defect_cart_coords, q=q,
        slab_bottom=slab_bottom, slab_top=slab_top, slab_buffer=slab_buffer,
    )
    out = optimize(
        q=q, sxdefectalign2d=sxdefectalign2d, encut=encut, vref=bulk_locpot, 
        vdef=defect_locpot, q=q, slab_bottom=slab_bottom, slab_buffer=slab_buffer,
        )
    es_corr, metadata = parse_output(out)
    return {"2d_electrostatic": es_corr}, metadata

    def write_sphinx_input(dielectric_const, lattice_matrix, defect_sc_coords, q, slab_bottom, slab_top, slab_buffer):
        """
        Write the sphinx (sxdefectalign2d) input file.

        Args:
            lattice_matrix: 3x3 matrix of lattice vectors in Angstrom.
            defect_sc_coords (list): Defect fractional coordinates w.r.t the lattice vectors
            q (float): Charge of defect (standard convention. 1 electron has q=-1)
            slab_bottom (float): Bottom of slab in Angstroms
            slab_top (float): Top of slab in Angstroms
        """
        lines = []
        lines.append("structure {\n")
        m = [list(l) for l in np.multiply(1.88973, lattice_matrix)]
        lines.append("\t cell = {};\n".format(str(m)))
        lines.append("}\n\n")

        lines.append("slab {\n")
        lines.append("\t fromZ = {};\n".format(1.88973 * slab_bottom))
        lines.append("\t toZ = {};\n".format(1.88973 * slab_top))
        lines.append("\t epsilon = {};\n".format(dielectric_const))
        lines.append("}\n\n")

        lines.append("charge {\n")
        lines.append("\t posZ = {};\n".format(1.88973 * defect_sc_coords[2]))
        lines.append("\t Q = {};\n".format(-q))
        lines.append("}\n\n")

        lines.append("isolated { \n")
        lines.append("\t fromZ = {};\n".format(1.88973 * (slab_bottom - slab_buffer)))
        lines.append("\t toZ = {};\n".format(1.88973 * (slab_top + slab_buffer)))
        lines.append("}\n\n")

        with open("system.sx", "w") as f:
            f.writelines(lines)

    def run_sxdefectalign2d(sxdefectalign2d, encut, vref, vdef, shift=0, C=0, only_profile=False):
        """
        Run sxdefectalign2d.

        Args:
            shift (ev): Shift in potential
            C (float): Alignment constant for potential
            only_profile (bool): If True, only determine the potential profile,
                and do not calculate the electrostatic energies.
        """

        command = [
            sxdefectalign2d,
            "--vasp",
            "--ecut",
            str(encut / 13.6057),  # convert eV to Ry
            "--vref",
            vref,
            "--vdef",
            vdef,
            "--shift",
            str(shift),
            "-C",
            str(C),
        ]
        if only_profile:
            command.append("--onlyProfile")

        with subprocess.Popen(command, stdout=subprocess.PIPE, stdin=subprocess.PIPE, close_fds=True) as rs:
            stdout, stderr = rs.communicate()
            if rs.returncode != 0:
                raise RuntimeError("exited with return code %d. " "Please check your installation." % rs.returncode)

            return stdout.decode("utf-8")

    def optimize(q, sxdefectalign, encut, vref, vdef, max_iter=1000, threshold_slope=1e-3, threshold_C=1e-3):
        """
        Optimize the alignment constant C and shift for the potentials.

        Args:
            q (float): Charge of defect
            max_iter (int): Maximum number of iterations
            threshold_slope (float): Threshold for slope
            threshold_C (float): Threshold for alignment constant
        """
        # initialize the range of shift values bracketing the optimal shift
        smin, smax = -np.inf, np.inf
        shift = 0.0
        shifting = "right"
        done = False
        counter = -1

        while not done and counter < max_iter:
            counter += 1
            run_sxdefectalign2d(sxdefectalign2d, encut, vref, vdef, shift=shift, C=0, only_profile=True)
            data = np.loadtxt("vline-eV.dat")

            # TODO assumes that the slab is in the center of the cell vertically
            # select datapoints corresponding to 2 bohrs at the top and bottom of the supercell
            z1 = np.min([i for i, z in enumerate(data[:, 0]) if z > 2.0])
            z2 = np.min([i for i, z in enumerate(data[:, 0]) if z > (data[-1, 0] - 2.0)])

            # fit straight lines through each subset of datapoints
            m1, C1 = np.polyfit(data[:z1, 0], data[:z1, -1], 1)
            m2, C2 = np.polyfit(data[z2:, 0], data[z2:, -1], 1)
            logger.debug("Slopes: %.8f %.8f; Intercepts: %.8f %.8f" % (m1, m2, C1, C2))

            # check the slopes and intercepts of the lines
            # and shift the charge along z until the lines are flat
            if abs(m1) < threshold_slope and abs(m2) < threshold_slope and abs(C1 - C2) < threshold_C:
                done = True
                break
            if m1 * m2 < 0:
                logger.info("undetermined...make a tiny shift and try again")
                if shifting == "right":
                    shift += 0.01
                else:
                    shift -= 0.01
                logger.info("try shift = %.8f" % shift)
            elif (m1 + m2) * np.sign(q) > 0:
                smin = shift
                if smax == np.inf:
                    shift += 1.0
                else:
                    shift = (smin + smax) / 2.0
                shifting = "right"
                logger.debug("optimal shift is in [%.8f, %.8f]" % (smin, smax))
                logger.info("shift charge in +z direction; try shift = %.8f" % shift)
            elif (m1 + m2) * np.sign(q) < 0:
                smax = shift
                if smin == -np.inf:
                    shift -= 1.0
                else:
                    shift = (smin + smax) / 2.0
                shifting = "left"
                logger.debug("optimal shift is in [%.8f, %.8f]" % (smin, smax))
                logger.info("shift charge in -z direction; try shift = %.8f" % shift)

        C_ave = (C1 + C2) / 2
        if done:
            logger.info("DONE! shift = %.8f & alignment correction = %.8f" % (shift, C_ave))
            return run_sxdefectalign2d(sxdefectalign2d, encut, vref, vdef, shift=shift, C=C_ave, only_profile=False)
        logger.info("Could not find optimal shift after %d tries :(" % max_iter)
        return run_sxdefectalign2d(sxdefectalign2d, encut, vref, vdef, shift=0, C=0, only_profile=False)

    def parse_output(output):
        """
        Parse the output of sxdefectalign2d. Including reading in the potential
        profiles from vline-eV.dat.

        Args:
            output (str): Output of sxdefectalign2d as a string.

        Returns: float corresponding to the correction in eV
        """
        data = np.loadtxt("vline-eV.dat")
        metadata = {"Vdiff": data[:, 2], "Vmodel": data[:, 1], "z": data[:, 0], "Vsr": data[:, 3]}
        return float(output.split("\n")[-2].split()[-2]), metadata

def plot_freysoldt2d(metadata, savefig=False):
    """
    Plot the potential profiles.

    Args:
        savefig (bool): If True, save the figure.
    """
    plt.figure()
    plt.plot(
        metadata["pot_plot_data"]["z"],
        metadata["pot_plot_data"]["Vdiff"],
        "r",
        label=r"$V_{def}-V_{bulk}$",
    )
    plt.plot(
        metadata["pot_plot_data"]["z"], metadata["pot_plot_data"]["Vmodel"], "g", label=r"$V_{model}$"
    )
    plt.plot(
        metadata["pot_plot_data"]["z"],
        metadata["pot_plot_data"]["Vsr"],
        "b",
        label=r"$V_{def}-V_{bulk}-V_{model}$",
    )
    plt.xlabel("distance along z axis (bohr)")
    plt.ylabel("potential (eV)")
    plt.xlim(metadata["pot_plot_data"]["z"].min(), metadata["pot_plot_data"]["z"].max())
    plt.legend()
    if savefig:
        plt.savefig("alignment.png")