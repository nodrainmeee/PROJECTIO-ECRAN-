# server_android — serveur Android d'AndroMirror (étape 2)

Petit programme **Java écrit pour AndroMirror** (aucun code de scrcpy ni d'un autre logiciel de miroir n'a été copié ;
la technique générale — programme lancé par `adb shell`, copie de l'écran par des API Android internes, encodage MediaCodec —
est celle de tous les logiciels de ce genre). Il n'est **pas installé** comme une application : le PC l'envoie dans
`/data/local/tmp`, le lance pour la durée d'une session, et il se supprime à la fin.

## Fichiers

| Fichier | Rôle |
|---|---|
| `src/com/andromirror/server/Server.java` | `main` : écoute sur un socket local abstrait, vérifie le jeton, envoie le flux, s'arrête quand le PC se déconnecte, supprime le `.jar` |
| `.../VideoStreamer.java` | encodage MediaCodec (H.264 / H.265), détection des changements de taille/rotation, envoi des paquets |
| `.../DisplayAccess.java` | **accès à l'écran par réflexion (API cachées)** : lecture de la taille/rotation, copie de l'écran (méthodes A puis B) |
| `.../FakeContext.java` | contexte « com.android.shell » minimal, uniquement pour la méthode de secours B |
| `.../Wire.java` | format des messages (sans dépendance à Android ; comparé octet par octet au code Python par un test) |
| `.../Options.java` | arguments `clé=valeur` reçus du PC (valeurs invalides → valeurs sûres) |
| `test/WireSelfTest.java` | affiche les messages de `Wire` en hexadécimal (test sur JDK de bureau) |
| `build.sh` | `javac --release 8` + `d8 --min-api 26` → `build/andromirror-server.jar` (contient `classes.dex`) |

## Construction

```
export ANDROID_HOME=/chemin/du/sdk      # avec platforms/android-34 et build-tools/34.0.0
bash server_android/build.sh
```

GitHub Actions le fait (tâche `server` de `.github/workflows/build.yml`, actions figées sur des SHA) et publie l'artefact
`andromirror-server`. Le `.exe` l'embarque dans son dossier `server/`. Le fichier produit n'est pas versionné.

## Déroulement d'une session

1. Le PC génère un **jeton aléatoire de 16 octets**, un nom de socket aléatoire (`andromirror_xxxxxxxx`) et un nom de fichier unique.
2. `adb push` du `.jar` → `/data/local/tmp/andromirror-server-xxxxxxxx.jar`.
3. `adb forward tcp:0 localabstract:andromirror_xxxxxxxx` : adb choisit un port libre sur `127.0.0.1`.
4. `adb shell CLASSPATH=<jar> app_process / com.andromirror.server.Server token=… socket=… codec=… bitrate=… fps=… max_short=… jar=…`
5. Le serveur écoute, **n'accepte que le client qui présente le jeton** (comparaison à durée constante), puis envoie le « bonjour »
   (nom du téléphone, codec, taille, rotation) et le flux. Délai d'attente de la connexion du PC : 30 s.
6. Fin : le PC se déconnecte (ou le câble est débranché) → le serveur le détecte (lecture du socket), arrête l'encodeur, détruit l'affichage
   virtuel, **supprime son `.jar`** et quitte. Le PC fait aussi `adb forward --remove` et `adb shell rm -f <jar>` (cas d'un arrêt brutal).

Protocole (gros-boutiste) — voir `Wire.java` / `andromirror/video/protocol.py` :
`bonjour` = `AMV1` + nom (64) + codec (4) + largeur/hauteur/rotation (3 × u32) ; puis des paquets `type u8 | indicateurs u8 | 0 u16 | pts u64 (µs) | longueur u32`
+ données. Types : vidéo (indicateurs : config, image clé), changement de taille/rotation, message (indicateur : erreur fatale). Le PC peut envoyer 1 octet :
demander une image clé, ou quitter.

## API Android cachées utilisées, et leur risque

Aucune de ces API n'est garantie par Google : elles peuvent changer ou être restreintes à chaque version d'Android, ou être modifiées par une
marque. Elles sont toutes appelées **par réflexion**, chaque appel est protégé, et un échec produit un message clair (sortie du serveur → journal du PC
`%APPDATA%\AndroMirror\logs\andromirror.log`, lignes « téléphone : … »).

| API (réflexion) | Utilisée pour | Versions visées | Risque |
|---|---|---|---|
| `DisplayManagerGlobal.getInstance().getDisplayInfo(0)` → champs `logicalWidth`, `logicalHeight`, `rotation`, `layerStack` | taille et rotation de l'écran, surveillées toutes les 250 ms | 8 → 15 | **Moyen** : noms de champs stables depuis des années, mais l'accès aux API non SDK peut être restreint sur certaines versions/marques ; sans cette lecture le serveur s'arrête avec un message |
| **Méthode A** : `SurfaceControl.createDisplay(String, boolean)`, `openTransaction()`, `closeTransaction()`, `setDisplaySurface(IBinder, Surface)`, `setDisplayProjection(IBinder, int, Rect, Rect)`, `setDisplayLayerStack(IBinder, int)`, `destroyDisplay(IBinder)` | copier l'écran vers la surface de l'encodeur | 8 → 14 en général | **Élevé** : c'est la partie la plus fragile ; peut échouer sur Android 14 récents/15 et sur certaines surcouches de marque |
| **Méthode B** (secours) : `ActivityThread` (contexte système) + `new DisplayManager(Context)` + `DisplayManager.createVirtualDisplay(String, int, int, int, Surface)` | copier l'écran quand la méthode A échoue | Android 14+ seulement | **Très élevé / jamais vérifié sur un appareil** : demande un faux contexte « com.android.shell » (`FakeContext`) ; peut être refusée (SecurityException) ou absente |

Autres réserves :

- **Jamais d'affichage « sécurisé »** : `createDisplay(..., false)`. Les écrans qui interdisent la capture apparaissent en noir. C'est voulu (pas de contournement de DRM).
  Conséquence possible : sur quelques appareils Android 8–11, la copie non sécurisée peut échouer ou rester noire.
- **Encodeur** : plusieurs essais de plus en plus prudents (profil High + faible latence → sans profil → sans options de latence → taille réduite à 1080 puis 720) ;
  taille ajustée aux capacités de l'encodeur. Les encodeurs matériels diffèrent beaucoup selon les marques (c'est la 2ᵉ source de problèmes).
- **Rotation** : détectée par scrutation (pas d'observateur système, pour éviter une API cachée de plus) ; un changement relance l'encodage à la nouvelle taille et
  prévient le PC (paquet « taille »). Il peut y avoir un bref saut d'image.
- **Un identifiant « shell »** : le serveur tourne avec les droits du shell ADB (uid 2000) et seulement le temps de la session.
- Android < 8 : non pris en charge (le PC l'indique). Le son n'est pas géré à cette étape.
