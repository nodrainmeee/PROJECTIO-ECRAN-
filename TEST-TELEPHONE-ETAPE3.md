# Test de la version 0.3.0 avec votre vrai téléphone

Rien de cette version n'a été essayé sur un vrai téléphone : **vos retours sont la vraie validation.**
Notez pour chaque ligne : ✅ ça marche, ❌ ça ne marche pas (et ce que vous voyez).

## 0. D'abord : le problème des coupures (film)

Vous avez signalé que l'affichage se coupait environ chaque seconde, surtout au lancement d'un film.
La version 0.3.0 relance l'encodeur au lieu de s'arrêter, et ne ferme plus un affichage qui fonctionne quand adb a un raté.
**Cause exacte non confirmée** : le journal décide.

| # | Action | Résultat attendu |
|---|---|---|
| 0.1 | Connecter, laisser l'écran d'accueil 1 minute | Aucune coupure |
| 0.2 | Lancer une vidéo locale (galerie) en paysage | L'image passe en paysage ; au pire un court message « Reprise de l'affichage… », puis ça repart |
| 0.3 | Lancer YouTube ou un film **non protégé** | Pareil ; image fluide |
| 0.4 | Si ça se coupe encore : **Réglages → Qualité** : résolution **1080p**, **30 i/s**, **H.264**, débit **8 Mb/s**, décocher le décodage par la carte graphique | Dites-moi si cela change quelque chose |
| 0.5 | Essayer un **autre câble** et un **port USB directement sur la carte mère** (pas un hub) | Idem |

**Envoyez-moi le journal dès qu'il y a une coupure** : `%APPDATA%\AndroMirror\logs\andromirror.log` (collez ce chemin dans
l'Explorateur Windows). Cherchez les lignes `Incident de l'encodeur`, `Fin de session`, `Téléphone perdu`, `coupure ignorée`, `téléphone : …`.
Indiquez le **modèle exact**, la **version d'Android** et ce que vous faisiez.

## 1. Contrôle

| # | Action | Résultat attendu |
|---|---|---|
| 1.1 | Cliquer sur une icône d'application dans la fenêtre | L'application s'ouvre sur le téléphone |
| 1.2 | Glisser avec la souris (haut/bas, gauche/droite) | Le téléphone défile / change de page |
| 1.3 | Molette de la souris sur une page longue | Défilement |
| 1.4 | **Clic droit** sur l'image | Retour |
| 1.5 | **Clic molette** sur l'image | Accueil |
| 1.6 | Boutons ◀ ● ■ de la barre d'outils | Retour, Accueil, Applications récentes |
| 1.7 | Boutons Vol − / Vol + | Le volume du téléphone change |
| 1.8 | ⏻ | L'écran du téléphone s'éteint / se rallume |
| 1.9 | Ouvrir un champ de texte et taper `bonjour 123` au clavier du PC | Le texte apparaît |
| 1.10 | Taper `é`, `à` | **Probablement ignoré** (limite connue) : dites-moi ce qui se passe |
| 1.11 | Entrée, Retour arrière, flèches dans un champ de texte | Ils agissent |
| 1.12 | Décocher **Contrôle** puis cliquer sur l'image | Rien ne se passe sur le téléphone ; le double-clic = plein écran |
| 1.13 | Tourner le téléphone en paysage, cliquer au centre | Le clic tombe au bon endroit |

Si le contrôle ne marche pas : un message « Le contrôle depuis le PC n'est pas disponible… » s'affiche, et le journal contient
`Contrôle : … inutilisable` ou `Injection impossible`. **Xiaomi/Redmi/POCO** : activez aussi « Débogage USB (paramètres de sécurité) ».

## 2. Son (Android 11 ou plus récent)

| # | Action | Résultat attendu |
|---|---|---|
| 2.1 | Réglages → Audio : « Envoyer le son… » coché, puis Connecter | Pas de message d'erreur |
| 2.2 | Lancer une vidéo avec du son sur le téléphone | **Le son sort sur le PC** |
| 2.3 | Écouter le téléphone | **Il reste muet** |
| 2.4 | Le son et l'image sont-ils d'accord ? | Écart de l'ordre d'un dixième de seconde (dites-moi si c'est gênant) |
| 2.5 | Régler le volume (Réglages → Audio) pendant la lecture | Change tout de suite |
| 2.6 | Lecture longue (10 minutes) | Pas de craquements ni de décalage qui grandit |
| 2.7 | Téléphone sous Android 10 ou plus ancien | Message clair : « demande Android 11 ou plus récent » ; l'image continue |

Si pas de son : message « Le son du téléphone n'a pas pu démarrer… » (la fin du message dit pourquoi), ou ligne `Son :` dans le journal.
Certaines applications interdisent la capture de leur son : essayez aussi la galerie ou YouTube.

## 3. Ce qui doit rester vrai

- Débrancher le câble : la fenêtre se ferme proprement ; rebranché, ça reprend.
- Déconnecter : plus de fichier `andromirror-server-*.jar` sur le téléphone, le son s'arrête.
- Contenus protégés (Netflix, banque) : écran noir (voulu).
