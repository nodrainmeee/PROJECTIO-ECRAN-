# Test de l'étape 2 avec votre vrai téléphone

Rien de ceci n'a été essayé sur un vrai téléphone par l'auteur : **vos retours sont la vraie validation.**
Prenez 10 minutes, dans cet ordre. Notez pour chaque point : ✅ ça marche, ❌ ça ne marche pas (et ce que vous voyez).

## Avant de commencer
1. Construisez le `.exe` (GitHub → Actions → **Build Windows** doit être **vert**, 2 tâches : *server* puis *build*) et téléchargez l'artefact **AndroMirror**.
2. Sur le téléphone : **Options pour les développeurs → Débogage USB** activé (l'assistant du logiciel vous guide). Chez Xiaomi/Redmi/POCO, activez aussi
   « Débogage USB (paramètres de sécurité) » si votre téléphone le propose.
3. Branchez le téléphone en USB (bon câble), déverrouillez-le, acceptez « Autoriser le débogage USB ? ».
4. Lancez `AndroMirror.exe`. Windows peut avertir (programme non signé) : *Informations complémentaires → Exécuter quand même*.

## À vérifier
| # | Action | Résultat attendu |
|---|---|---|
| 1 | Le logiciel affiche *Téléphone détecté* avec le bon modèle | (c'était déjà l'étape 1) |
| 2 | Cliquez **Connecter** | Une 2ᵉ fenêtre s'ouvre avec « Connexion au téléphone… » puis **l'écran du téléphone apparaît** en 2–5 s |
| 3 | Touchez l'écran du téléphone, ouvrez une appli, faites défiler | L'image bouge sur le PC (un petit délai est normal, **sans contrôle depuis le PC** : souris et clavier ne font rien pour l'instant) |
| 4 | Barre d'état de la fenêtre | Affiche par ex. `1080×2400 · H.264 · décodage logiciel (ou matériel) · 60 i/s` |
| 5 | Tournez le téléphone en **paysage** (lancez une vidéo plein écran) | L'image passe en paysage et **la fenêtre se redimensionne toute seule** (court saut d'image possible) |
| 6 | Appuyez sur **F11** (ou le bouton *Plein écran*) | Plein écran ; **Échap** revient à la fenêtre (Échap ne ferme pas la fenêtre) |
| 7 | **Débranchez le câble** pendant l'affichage | La fenêtre se ferme, message « Téléphone débranché : l'affichage a été fermé… », **le logiciel ne plante pas** |
| 8 | Rebranchez (reconnexion automatique activée dans les réglages) | L'affichage **reprend tout seul** |
| 9 | Cliquez **Déconnecter** (ou fermez la fenêtre d'affichage) | La fenêtre se ferme, plus d'image |
| 10 | Ouvrez une application bancaire ou une vidéo de streaming protégée | **Écran noir : c'est normal et voulu** (pas de contournement) |
| 11 | Réglages → Qualité : choisissez une résolution plus basse (ex. 1080p) et moins d'images par seconde, puis Connecter | L'image est plus légère ; la barre d'état le confirme |
| 12 | Réglages → Qualité : choisissez **H.265**, puis Connecter | Si le téléphone ne sait pas, un message d'erreur clair apparaît (dites-le-moi) |
| 13 | Après Déconnecter : `adb shell ls /data/local/tmp` (ou ignorez) | Aucun fichier `andromirror-server-*.jar` ne doit rester |

## Si ça ne marche pas : envoyez-moi le journal
Fichier : **`%APPDATA%\AndroMirror\logs\andromirror.log`** (collez ce chemin dans l'Explorateur Windows),
ou menu **Aide → Ouvrir le dossier du journal d'erreurs**. Il contient les lignes `téléphone : …` écrites par le serveur Android, et pour chaque problème :
- le **modèle exact** du téléphone, sa **version d'Android** et sa **marque** ;
- ce que vous voyiez (écran noir ? fenêtre vide ? message ?) ;
- à quel numéro du tableau ça a cassé.

Cas probables, déjà prévus : *écran noir* (méthode de capture refusée par la marque, ou contenu protégé) ; *« Le serveur du téléphone n'a pas démarré »*
(la dernière ligne du serveur est affichée) ; *image saccadée* (essayez une résolution plus basse, ou décochez le décodage par la carte graphique) ; *couleurs bizarres*.
