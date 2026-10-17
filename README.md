# AndroMirror

Logiciel Windows (Python 3.12 + PySide6) pour **afficher et contrôler un téléphone Android depuis un PC** via ADB.
Nom provisoire, version **0.2.0 — étape 2 sur la feuille de route (affichage de l'écran)**.

> ⚠️ **État actuel : étapes 1 et 2.** Le logiciel détecte le téléphone et **affiche son écran dans une fenêtre du PC**
> (mode portrait/paysage automatique, plein écran avec F11, qualité réglable). **Le contrôle à la souris et le son ne
> sont pas encore là** (étapes suivantes ; les boutons de la barre d'outils sont des emplacements désactivés).
> Tout a été testé avec un **faux adb** et un **faux téléphone** (tests automatiques) :
> **rien n'a été essayé sur un vrai téléphone ni sur un vrai Windows par l'auteur de cette étape.** L'étape 2 est la
> plus risquée du projet : voir « Limites connues » et `TEST-TELEPHONE-ETAPE2.md`.

## Ce que fait l'étape 2 (affichage)

- **Connecter** : envoie un petit serveur Java (`andromirror-server.jar`, écrit pour AndroMirror) dans `/data/local/tmp` du
  téléphone, le lance par `adb shell` (`app_process`), relie un port local du PC par `adb forward`, puis affiche l'écran
  dans une **fenêtre séparée**.
- **Image** : le téléphone encode son écran en **H.264** (ou **H.265** si choisi dans les réglages) avec son encodeur matériel
  (MediaCodec) ; le PC décode avec **PyAV (FFmpeg)** — **accélération graphique essayée d'abord** (Direct3D 11, DXVA2, CUDA,
  Quick Sync), **décodage logiciel sinon** — et dessine avec **OpenGL** (repli automatique sans OpenGL).
- **Faible latence** : une seule « dernière image » est gardée ; si le PC prend du retard, les anciennes images sont jetées,
  le décodeur attend la prochaine image clé et la demande au téléphone (jamais d'image déformée, jamais de file qui grossit).
- **Orientation** : quand le téléphone passe de portrait à paysage (ou inversement), la vidéo change de taille et la fenêtre
  s'adapte toute seule au nouveau format.
- **Plein écran** : **F11** ou bouton *Plein écran* (ou double-clic sur l'image) ; **Échap** pour en sortir. Échap ne ferme jamais la fenêtre.
- **Qualité** : résolution (native → 4K, 1440p, 1080p, 720p, limite du petit côté), débit 4–60 Mb/s, 30/60 images/s, codec, et
  option « décodage par la carte graphique » (Réglages → Qualité). Appliqué à la **prochaine** connexion.
- **Déconnecter**, **fermer la fenêtre d'affichage** ou **débrancher le câble** : l'affichage se ferme proprement avec un
  message en français ; le serveur s'arrête et **se supprime du téléphone**. Si la reconnexion automatique est active, l'affichage
  reprend tout seul au rebranchement.
- **Confidentialité / sécurité** : aucune donnée ne quitte le PC et le téléphone (tout passe par le câble USB, sur
  `127.0.0.1`). Le flux n'est accepté que d'un client qui présente un **jeton secret aléatoire de 16 octets** (différent à chaque
  session) sur un socket au nom aléatoire : une autre application du téléphone ou un autre programme du PC ne peut pas voir l'écran.
- **Protections de contenu respectées** : l'écran est copié en mode **non sécurisé** — les applications qui interdisent la
  capture (certaines applications bancaires, vidéos sous DRM comme les services de streaming) apparaissent **en noir**.
  AndroMirror ne contourne pas ces protections.

Ce que l'étape 2 **ne fait pas** : contrôle souris/clavier, son, Wi-Fi, option « garder l'écran du téléphone éteint » (réglage
sauvegardé mais sans effet), enregistrement.

## Ce que fait l'étape 1 (rappel)

- **Détection** du téléphone branché en USB (`adb devices -l`), lecture du modèle, de la version d'Android et de la taille d'écran (`wm size`).
- **États clairs** : *Non connecté*, *En attente d'autorisation*, *Connecté*, *Erreur* (avec un message qui explique quoi faire).
- Bouton **Connecter / Déconnecter** (depuis l'étape 2, *Connecter* ouvre aussi l'affichage).
- **Détection de débranchement et de rebranchement** (base de la reconnexion automatique) : si vous êtes connecté et que le câble est retiré, le logiciel attend et reprend tout seul au rebranchement (désactivable dans les réglages).
- **Assistant de première utilisation en 3 étapes** : options pour les développeurs → débogage USB → brancher et accepter. Il s'ouvre au premier lancement et reste accessible à tout moment.
- **Thème clair / sombre** (ou automatique comme Windows).
- **Réglages** (qualité vidéo, audio, général) enregistrés automatiquement. Depuis l'étape 2, les réglages de qualité sont **appliqués** à la connexion suivante ; les réglages d'audio sont sauvegardés mais ne font encore rien.
- Tout ce qui parle à ADB tourne dans un **thread séparé** : la fenêtre ne se fige jamais, même si ADB ne répond plus.
- **Journal d'erreurs** (fichier) et message clair en cas d'erreur inattendue.

## Où sont les fichiers de l'utilisateur ?

| Quoi | Où |
|---|---|
| Réglages | `%APPDATA%\AndroMirror\settings.json` |
| Journal d'erreurs | `%APPDATA%\AndroMirror\logs\andromirror.log` (menu **Aide → Ouvrir le dossier du journal d'erreurs**) |

Si le fichier de réglages est abîmé, il est renommé en `settings.json.corrompu` et le logiciel repart avec les valeurs par défaut.

## Structure du projet

```
AndroMirror/
├─ main.py                     point d'entrée
├─ andromirror/
│  ├─ connection/              adb embarqué, détection, surveillance (thread)   ← étape 1
│  │  └─ server_launcher.py    envoi / lancement / nettoyage du serveur Android  ← étape 2
│  ├─ ui/                      fenêtre principale, assistant, réglages, thèmes  ← étape 1
│  │  └─ display_window.py, display_manager.py   fenêtre d'affichage, plein écran  ← étape 2
│  ├─ video/                   protocole, récepteur, décodeur PyAV, file à faible latence, OpenGL  ← étape 2
│  ├─ audio/                   (réservé) son du téléphone vers le PC
│  ├─ control/                 (réservé) souris, clavier, boutons système
│  ├─ settings.py              réglages JSON (validation, sauvegarde atomique)
│  ├─ logging_setup.py         journal d'erreurs
│  └─ paths.py                 chemins (ressources, dossier utilisateur)
├─ server_android/             serveur Java (MediaCodec, API Android cachées par réflexion)  ← étape 2
├─ build/                      installateur Inno Setup, script de build local
├─ tests/                      tests unitaires + test de fumée de l'interface (faux adb)
├─ tools/                      captures d'écran (faux téléphone) et contrôle de licence FFmpeg
├─ assets/                     icône
├─ third_party/                avis de licences des composants tiers
├─ TEST-TELEPHONE-ETAPE2.md    liste de vérification à faire avec un vrai téléphone
└─ .github/workflows/build.yml construction du .exe sur GitHub
```

## Lancer en développement (Windows ou Linux, Python 3.12)

```
pip install -r requirements.txt
python main.py
```

Sans dossier `platform-tools/` à la racine, le programme utilise l'`adb` du PATH (développement uniquement). La variable `ANDROMIRROR_ADB` permet d'indiquer un autre adb (utilisée par les tests).

## Tests

```
pip install -r requirements.txt -r requirements-dev.txt
set QT_QPA_PLATFORM=offscreen        (Windows : set ; Linux : export)
python -m pytest -q
```

Aucun vrai téléphone n'est nécessaire (ni utilisé) :

- `tests/fake_adb.py` : faux adb piloté par un fichier JSON (téléphone prêt, non autorisé, hors ligne, débranché/rebranché, adb absent
  ou bloqué, `push`/`forward`/lancement du serveur qui réussissent ou échouent…).
- `tests/fake_server.py` : **faux téléphone** qui parle le même protocole que le serveur Java, par un socket local, avec des images
  synthétiques (`tests/synth.py` : mire de couleurs encodée en H.264/H.265 par PyAV). Il sait refuser les premières connexions,
  couper brutalement, se bloquer, envoyer une erreur fatale, changer d'orientation, compter les demandes d'image clé.
- Protocole (y compris la **comparaison octet par octet avec le code Java**, si un JDK est installé), décodage H.264/H.265, file de
  paquets et abandon des anciennes images, récepteur réseau, lancement/nettoyage du serveur, widget (rapport largeur/hauteur),
  session de bout en bout, fenêtre d'affichage hors écran (plein écran, Échap, orientation), fenêtre principale (Connecter, Déconnecter,
  débranchement, reprise, échec de lancement), auto-test.
- L'interface est testée hors écran (`QT_QPA_PLATFORM=offscreen`) : **le rendu OpenGL réel d'une carte graphique n'est donc pas testé** (repli logiciel utilisé par Qt hors écran).

## Serveur Android (Java)

Voir `server_android/README.md` : fonctionnement, protocole, **API Android cachées utilisées par réflexion et leur risque selon
les versions d'Android**, construction (`bash server_android/build.sh` : `javac` + `d8`, plateforme `android-34`, build-tools 34.0.0).
Le fichier produit, `server_android/build/andromirror-server.jar`, est généré par GitHub Actions et embarqué dans le `.exe`
(il n'est pas dans le dépôt).

## Construire le .exe (GitHub Actions)

1. Déposez **tout le contenu** de ce dossier dans un dépôt GitHub, sur la branche `main`, **y compris le dossier caché `.github`** (sous Windows, activez « Éléments masqués » dans l'Explorateur). Le fichier doit s'appeler exactement `.github/workflows/build.yml` (sans point à la fin).
2. Onglet **Actions** → le workflow **Build Windows** se lance à chaque dépôt sur `main` (ou bouton **Run workflow**).
3. Le workflow a **deux tâches** :
   - **server** (Linux) : installe Java 17 et le SDK Android (plateforme android-34, build-tools 34.0.0) → compile le serveur avec `javac` puis `d8` → publie l'artefact **andromirror-server** (`andromirror-server.jar` + son SHA-256).
   - **build** (Windows) : récupère le serveur et **vérifie son SHA-256** → installe Python 3.12 et les dépendances (dont PyAV) → **contrôle de licence FFmpeg** (avertissement si la roue PyAV contient des bibliothèques GPL) → lance les tests → télécharge **platform-tools r37.0.1** et **vérifie son SHA-256** → construit `AndroMirror.exe` avec PyInstaller (un seul fichier, sans console, adb + serveur + FFmpeg embarqués) → lance un **auto-test du .exe** (`AndroMirror.exe --selftest` : adb embarqué, fenêtre, **FFmpeg (encode puis décode une image H.264)**, **présence et validité du serveur embarqué**, fenêtre d'affichage) → publie l'artefact **AndroMirror**.
4. Les actions GitHub sont figées sur des commits précis (pas sur des étiquettes modifiables).

### Installateur (optionnel)

`build/AndroMirror.iss` est un script **Inno Setup 6** (installation par utilisateur, raccourcis, désinstallation propre ; réglages et journal conservés). Dans **Actions → Build Windows → Run workflow**, cochez *Créer aussi l'installateur* : l'artefact **AndroMirror-Installateur** (`AndroMirror-Setup.exe`) est alors produit. Il n'est pas construit lors des dépôts normaux.

Construction locale : `build/build_local.ps1` (voir l'en-tête du fichier).

## Licences et composants tiers

- **ADB (Android Debug Bridge / Android SDK Platform-Tools)** — © The Android Open Source Project, **licence Apache 2.0** (<https://www.apache.org/licenses/LICENSE-2.0>). AndroMirror embarque `adb.exe`, `AdbWinApi.dll` et `AdbWinUsbApi.dll` **sans modification**, téléchargés depuis Google (r37.0.1) avec vérification d'empreinte SHA-256. Le fichier `NOTICE.txt` de Google est inclus. L'avis complet figure dans `third_party/AVIS-LICENCES.txt` (affiché aussi par l'installateur) et dans la boîte **Aide → À propos**.
- **PySide6 / Qt for Python (Qt 6)** — © The Qt Company, **licence LGPL v3** (<https://www.gnu.org/licenses/lgpl-3.0.html>). PySide6 et Qt sont utilisés **sans modification** et liés **dynamiquement** : les bibliothèques Qt sont embarquées comme fichiers séparés (DLL) dans l'exécutable PyInstaller `--onefile`, qui les extrait dans un dossier temporaire au lancement. Vous pouvez les remplacer en reconstruisant l'exécutable avec une autre version de PySide6 à partir du code source d'AndroMirror et de `requirements.txt`. Code source de Qt : <https://code.qt.io/> ; de PySide6 : <https://code.qt.io/cgit/pyside/pyside-setup.git>. Cette mention est donnée à titre d'information, ce n'est pas un avis juridique.
- **PyAV** (licence BSD 3 clauses) et **FFmpeg** — AndroMirror embarque la roue officielle `av` 19.0.1 (PyAV), qui contient FFmpeg (libavcodec 63.1) sous forme de DLL. FFmpeg est sous **LGPL** ; libavcodec de cette roue se déclare « **LGPL version 3 or later** ». **Point d'attention (vérifié sur la roue Windows et la roue Linux)** : cette roue contient aussi **libx264 et libx265, qui sont sous GPL v2 ou ultérieure**, ainsi que d'autres bibliothèques (dav1d, libvpx, opus, SVT-AV1, libwebp, GnuTLS/Nettle/GMP...). **AndroMirror n'utilise aucun encodeur** (il ne fait que décoder le H.264/H.265 reçu du téléphone, avec les décodeurs natifs de FFmpeg), mais **les fichiers GPL sont livrés dans le `.exe`**. Pour un **usage personnel sans distribution**, la GPL n'impose rien ; **si vous diffusez le `.exe` à d'autres personnes**, il faut soit respecter la GPL pour l'ensemble (publier le code source complet d'AndroMirror sous une licence compatible et proposer les sources de FFmpeg/x264/x265), soit **remplacer la roue PyAV par une version construite avec un FFmpeg sans GPL** (`python tools/check_ffmpeg_license.py --strict` le vérifie). Voir `third_party/AVIS-LICENCES.txt`. Ce n'est pas un avis juridique.
- AndroMirror n'est pas affilié à Google. « Android » est une marque de Google LLC.

## Limites connues (étapes 1 et 2)

- **Jamais essayé sur un vrai téléphone ni sur un vrai Windows par l'auteur.** Les parties les plus risquées, dans l'ordre :
  1. **Les API Android cachées** du serveur (`SurfaceControl`, `DisplayManagerGlobal`, `DisplayManager`) : leur forme change selon la
     version d'Android et la marque ; la méthode de secours pour Android 14+ est la moins sûre. Voir `server_android/README.md`.
  2. **Les différences entre marques** (Samsung, Xiaomi, Tecno, Infinix, Huawei/Honor…) : encodeurs MediaCodec, profils H.264, réglages de
     sécurité USB (par exemple « Débogage USB (paramètres de sécurité) » chez Xiaomi/Redmi/POCO, qui peut être nécessaire).
  3. **Le décodage par la carte graphique** : essayé puis abandonné en cas d'échec, mais jamais vu fonctionner par l'auteur.
  4. **Le rendu OpenGL** réel (testé seulement en rendu logiciel hors écran).
  5. Les performances en **4K / 60 images/s** (conversion couleur et copie en mémoire côté PC ; aucune mesure sur un vrai PC).
- **Contenus protégés** (DRM, applications sensibles) : image noire, c'est voulu.
- **USB uniquement** : la connexion Wi-Fi (appairage) n'est pas faite.
- **Un seul téléphone** géré à la fois ; s'il y en a plusieurs, le premier prêt est utilisé (un message l'indique).
- Sous Windows, certains téléphones peuvent demander le pilote USB du constructeur.
- Le `.exe` n'est **pas signé** : Windows SmartScreen peut afficher un avertissement au premier lancement. Il est lourd (~100 Mo) car il contient Qt et FFmpeg.
- **Couleurs** : quand le flux ne précise pas son espace colorimétrique, AndroMirror suppose BT.709 (comme un lecteur vidéo pour le HD) ; de légères différences de teinte selon les téléphones sont possibles. Pas de HDR.
- Interface en français uniquement.

## Feuille de route

1. **Étape 1** : connexion, détection, interface de base, réglages, build.
2. **Étape 2 (ici)** : serveur Android (Java, MediaCodec), flux vidéo, décodage PyAV, affichage OpenGL, orientation, plein écran, réglages de qualité.
3. Contrôle (souris, clavier, boutons système).
4. Audio (Android 11+).
5. Wi-Fi (appairage), reconnexion automatique complète.
6. Finalisation : tests multi-téléphones, installateur, manuels.
