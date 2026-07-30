"""Cœur métier partagé de BLDEMAT-SCAN (BL créés depuis les scans du copieur).

Utilisé à l'identique par l'app Administration et par les jobs Lakeflow : la
règle de décision (`decision.py`) et l'extraction IA (`extraction.py`) ne sont
écrites qu'une fois.

`shared/bl_core` est la source de vérité. Après modification, resynchroniser la
copie embarquée par l'application ::

    cp shared/bl_core/*.py src/app_administration/bl_core/
"""

__version__ = "6.0.0-scan"
