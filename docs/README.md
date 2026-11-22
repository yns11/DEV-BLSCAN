# Documentation utilisateur

- `Guide_rapide_BL.pdf` — guide rapide des principales fonctionnalités (2 pages).
- `generer_guide.py` — source du guide. Le régénérer après toute évolution des
  écrans, pour que les libellés cités restent ceux de l'application :

  ```bash
  pip install reportlab
  python docs/generer_guide.py docs/Guide_rapide_BL.pdf
  ```

  Polices DejaVu attendues dans `/usr/share/fonts/truetype/dejavu/` (présentes
  par défaut sur la plupart des distributions Linux).
