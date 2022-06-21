"""Classes representing defects."""
from __future__ import annotations

import logging
from abc import ABCMeta, abstractmethod, abstractproperty
from typing import Dict

import numpy as np
from monty.json import MSONable
from pymatgen.analysis.structure_matcher import ElementComparator, StructureMatcher
from pymatgen.core import Element, PeriodicSite, Species, Structure
from pymatgen.symmetry.analyzer import SpacegroupAnalyzer
from pymatgen.symmetry.structure import SymmetrizedStructure

from pymatgen.analysis.defect.supercells import get_sc_fromstruct

__author__ = "Jimmy-Xuan Shen"
__copyright__ = "Copyright 2022, The Materials Project"
__maintainer__ = "Jimmy Shen @jmmshn"
__date__ = "Mar 15, 2022"

logger = logging.getLogger(__name__)


class Defect(MSONable, metaclass=ABCMeta):
    """Abstract class for a single point defect."""

    def __init__(
        self,
        structure: Structure,
        site: PeriodicSite,
        multiplicity: int | None = None,
        oxi_state: float | None = None,
        symprec: float = 0.01,
        angle_tolerance: float = 5,
    ) -> None:
        """Initialize a defect object.

        Args:
            structure: The structure of the defect.
            site: The site
            charge: The charge of the defect.
            multiplicity: The multiplicity of the defect.
            oxi_state: The oxidation state of the defect, if not specified, this will be determined automatically.
            symprec: Tolerance for symmetry finding.
            angle_tolerance: Angle tolerance for symmetry finding.
        """
        self.structure = structure
        self.site = site
        self.symprec = symprec
        self.angle_tolerance = angle_tolerance
        self.multiplicity = multiplicity if multiplicity is not None else self.get_multiplicity()
        if oxi_state is None:
            # TODO this step might take time so wrap it in a timer
            self.structure.add_oxidation_state_by_guess()
        self.oxi_state = self.get_oxi_state() if oxi_state is None else oxi_state

    @abstractmethod
    def get_multiplicity(self) -> int:
        """Get the multiplicity of the defect.

        Returns:
            int: The multiplicity of the defect.
        """

    @abstractmethod
    def get_oxi_state(self) -> float:
        """Get the oxidation state of the defect.

        Returns:
            float: The oxidation state of the defect.
        """

    @abstractmethod
    def __repr__(self) -> str:
        """Representation of the defect."""

    @abstractproperty
    def name(self) -> str:
        """Name of the defect."""

    @abstractproperty
    def defect_structure(self) -> Structure:
        """Get the unit-cell structure representing the defect."""

    @abstractproperty
    def element_changes(self) -> Dict[Element, int]:
        """Get the species changes of the defect.

        Returns:
            Dict[Element, int]: The species changes of the defect.
        """

    def get_charge_states(self):
        """Potential charge states for a given oxidation state.

        Returns:
            list of possible charge states
        """
        if self.oxi_state.is_integer():
            oxi_state = int(self.oxi_state)
        else:
            raise ValueError("Oxidation state must be an integer")

        if oxi_state >= 0:
            charges = [*range(-1, oxi_state + 2)]
        else:
            charges = [*range(oxi_state - 1, 2)]

        return charges

    def get_supercell_structure(
        self, sc_mat: np.ndarray | None = None, dummy_species: str | None = None, **kwargs
    ) -> Structure:
        """Generate the supercell for a defect.

        Args:
            defect: defect object
            sc_mat: supercell matrix
            dummy_species: dummy species to highlight the defect position (for visualization)
            kwargs: kwargs for `CubicSupercellTransformation`

        Returns:
            defect: defect object
        """
        if sc_mat is None:
            sc_mat = get_sc_fromstruct(self.structure, **kwargs)

        sc_structure = self.structure * sc_mat
        sc_mat_inv = np.linalg.inv(sc_mat)
        sc_pos = np.dot(self.site.frac_coords, sc_mat_inv)
        sc_site = PeriodicSite(self.site.species, sc_pos, sc_structure.lattice)

        sc_defect = self.__class__(structure=sc_structure, site=sc_site, oxi_state=self.oxi_state)
        sc_defect_struct = sc_defect.defect_structure
        sc_defect_struct.remove_oxidation_states()
        if dummy_species is not None:
            dummy_pos = np.dot(self.site.frac_coords, sc_mat_inv)
            dummy_pos = np.mod(dummy_pos, 1)
            sc_defect_struct.insert(len(sc_structure), dummy_species, dummy_pos)

        return sc_defect_struct

    @property
    def symmetrized_structure(self) -> SymmetrizedStructure:
        """Returns the multiplicity of a defect site within the structure.

        This is required for concentration analysis and confirms that defect_site is a site in bulk_structure.
        """
        sga = SpacegroupAnalyzer(self.structure, symprec=self.symprec, angle_tolerance=self.angle_tolerance)
        return sga.get_symmetrized_structure()

    def __eq__(self, __o: object) -> bool:
        """Equality operator."""
        if not isinstance(__o, Defect):
            raise TypeError("Can only compare Defects to Defects")
        sm = StructureMatcher(comparator=ElementComparator())
        return sm.fit(self.defect_structure, __o.defect_structure)


class Vacancy(Defect):
    """Class representing a vacancy defect."""

    def get_multiplicity(self) -> int:
        """Returns the multiplicity of a defect site within the structure.

        This is required for concentration analysis and confirms that defect_site is a site in bulk_structure.
        """
        symm_struct = self.symmetrized_structure
        defect_site = self.structure[self.defect_site_index]
        equivalent_sites = symm_struct.find_equivalent_sites(defect_site)
        return len(equivalent_sites)

    @property
    def name(self) -> str:
        """Name of the defect."""
        return f"Va_{get_element(self.defect_site.specie)}"

    @property
    def defect_site(self):
        """Returns the site in the structure that corresponds to the defect site."""
        res = min(
            self.structure.get_sites_in_sphere(self.site.coords, 0.1, include_index=True),
            key=lambda x: x[1],
        )
        if len(res) == 0:
            raise ValueError("No site found in structure")
        return res

    @property
    def defect_site_index(self) -> int:
        """Get the index of the defect in the structure."""
        return self.defect_site.index

    @property
    def defect_structure(self):
        """Returns the defect structure with the proper oxidation state."""
        struct = self.structure.copy()
        struct.remove_sites([self.defect_site_index])
        return struct

    @property
    def element_changes(self) -> Dict[Element, int]:
        """Get the species changes of the vacancy defect.

        Returns:
            Dict[Element, int]: The species changes of the defect.
        """
        return {self.structure.sites[self.defect_site_index].specie.element: -1}

    def get_oxi_state(self) -> float:
        """Get the oxidation state of the defect.

        Returns:
            float: The oxidation state of the defect.
        """
        return -self.defect_site.specie.oxi_state

    def __repr__(self) -> str:
        """Representation of a vacancy defect."""
        vac_species = get_element(self.defect_site.specie)
        return f"{vac_species} Vacancy defect at site #{self.defect_site_index}"


class Substitution(Defect):
    """Single-site substitutional defects."""

    def __init__(
        self,
        structure: Structure,
        site: PeriodicSite,
        multiplicity: int | None = None,
        oxi_state: float | None = None,
        **kwargs,
    ) -> None:
        """Initialize a substitutional defect object.

        The position of `site` determines the atom to be removed and the species of `site` determines the replacing species.

        Args:
            structure: The structure of the defect.
            site: replace the nearest site with this one.
            multiplicity: The multiplicity of the defect.
            oxi_state: The oxidation state of the defect, if not specified, this will be determined automatically.
        """
        super().__init__(structure, site, multiplicity, oxi_state, **kwargs)

    def get_multiplicity(self) -> int:
        """Returns the multiplicity of a defect site within the structure.

        This is required for concentration analysis and confirms that defect_site is a site in bulk_structure.
        """
        sga = SpacegroupAnalyzer(self.structure)
        periodic_struc = sga.get_symmetrized_structure()
        defect_site = self.structure[self.defect_site_index]
        equivalent_sites = periodic_struc.find_equivalent_sites(defect_site)
        return len(equivalent_sites)

    @property
    def name(self) -> str:
        """Name of the defect."""
        return f"{get_element(self.site.specie)}_{get_element(self.defect_site.specie)}"

    @property
    def defect_structure(self):
        """Returns the defect structure."""
        struct: Structure = self.structure.copy()
        rm_oxi = struct.sites[self.defect_site_index].specie.oxi_state
        struct.remove_sites([self.defect_site_index])
        sub_states = self.site.specie.icsd_oxidation_states
        if len(sub_states) == 0:
            sub_states = self.site.specie.oxidation_states
        sub_oxi = min(sub_states, key=lambda x: abs(x - rm_oxi))
        sub_specie = Species(self.site.specie.symbol, sub_oxi)
        struct.insert(self.defect_site_index, species=sub_specie, coords=np.mod(self.site.frac_coords, 1))
        return struct

    @property
    def defect_site(self):
        """Returns the site in the structure that corresponds to the defect site."""
        return min(
            self.structure.get_sites_in_sphere(self.site.coords, 0.1, include_index=True),
            key=lambda x: x[1],
        )

    @property
    def defect_site_index(self) -> int:
        """Get the index of the defect in the structure."""
        return self.defect_site.index

    @property
    def element_changes(self) -> Dict[Element, int]:
        """Get the species changes of the substitution defect.

        Returns:
            Dict[Element, int]: The species changes of the defect.
        """
        return {
            self.structure.sites[self.defect_site_index].specie.element: -1,
            self.site.specie.element: +1,
        }

    def get_oxi_state(self) -> float:
        """Get the oxidation state of the defect.

        Returns:
            float: The oxidation state of the defect.
        """
        orig_site = self.defect_site
        sub_site = self.defect_structure[self.defect_site_index]
        return sub_site.specie.oxi_state - orig_site.specie.oxi_state

    def __repr__(self) -> str:
        """Representation of a vacancy defect."""
        rm_species = get_element(self.defect_site.specie)
        sub_species = get_element(self.site.specie)
        return f"{sub_species} subsitituted on the {rm_species} site at at site #{self.defect_site_index}"


def get_element(sp_el: Species | Element) -> Element:
    """Get the element from a species or element."""
    if isinstance(sp_el, Species):
        return sp_el.element
    return sp_el
