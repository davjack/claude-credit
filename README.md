# claude-credit

*Version française : plus bas sur la page GitHub, et dans le guide PDF en français.*

This document explains how to install and use `claude-credit`, a free software tool published by Davjack on [GitHub](https://github.com/davjack/claude-credit). It lets you keep working in Claude Code while paying with an Anthropic Console API key: the monthly API credit of a Max, Ultra or Team plan, or an ordinary API account.

For now, the installer and `claude-credit` itself display their messages in French: this guide quotes them as they appear, with their meaning in English. Improvements will follow (see “Roadmap”).

## What claude-credit does

Interactive Claude Code cannot be paid for with a plan's monthly API credit. That credit does cover `claude -p`, Claude Code's non-interactive mode, run with an API key ([Anthropic's page](https://support.claude.com/en/articles/17154008-monthly-api-credits-for-max-ultra-and-team-plans), read on 17 November 2026).

`claude-credit` keeps a single `claude -p` open for the whole session and passes it your messages. Claude Code works as usual: same sessions, same hooks, same skills, same `CLAUDE.md`. The display looks like Claude Code's: a formatted answer, one line per action, the cost at the end of each turn.

The model is Opus 5.5 with a one-million-token context. No permission is asked during the session: a firewall blocks destructive commands (see “Security”).

## What you need

- A computer running Linux, macOS 13 or later, or Windows 10 (version 2004 or later) or 11 with WSL.
- Python 3.9 or later (`claude-credit` has been tried with Python 3.9 and 3.14 on Linux).
- Claude Code, installed and up to date.
- Your own Anthropic Console API key, which starts with `sk-ant-api`.

Depending on the system:

- Linux: everything works.
- macOS: should work, except the process guardian (see “Security”) and the end-of-turn notification, which are Linux-only. Not tried on a Mac as of this guide's date.
- Windows: `claude-credit` runs in WSL, the Linux built into Windows, as on Linux. Windows alone, without WSL, is not supported: `claude-credit` needs Unix's terminal and process management.

## Getting an API key

There are two cases.

With a Max, Ultra or Team plan:

1. On claude.ai, open “Settings > Billing” (for Team: “Organization settings > Billing”).
2. Under “API credits”, choose “Link organization”, then the Console organization that will receive the credit, or create one.
3. In that organization, create a key on the Console's [API keys](https://platform.claude.com/settings/keys) page.

The credit is a fixed monthly amount. Whatever is not used is lost at the end of the billing cycle. Once the credit is spent, requests stop until the next credit, unless the organization has bought credits or turned on auto-reload. Nothing is ever charged to the plan.

With an ordinary API account, each session is paid for:

1. Create an account on [platform.claude.com](https://platform.claude.com) and buy credits (“Billing” page).
2. Set a monthly spend limit on the [Spend limits](https://platform.claude.com/settings/spend-limits) page.
3. Create a key on the [API keys](https://platform.claude.com/settings/keys) page.

A new organization sometimes starts with rate limits lower than the standard ones ([Anthropic's page](https://platform.claude.com/docs/en/api/rate-limits), read on 17 November 2026). The “nouvel essai” (retry) lines displayed then are Claude Code retrying.

WARNING: never export `ANTHROPIC_API_KEY` in the terminal, nor in `~/.bashrc` or `~/.zshrc`. Interactive Claude Code would then use the key instead of the subscription, once the key is approved ([Anthropic's page](https://code.claude.com/docs/en/authentication), read on 17 November 2026). `claude-credit` asks for the key at each launch and gives it only to its own `claude`.

## Preparing your system

Linux: check that `python3 --version` shows 3.9 or later.

macOS:

1. Open Terminal (Applications > Utilities > Terminal).
2. Type `python3 --version`. If macOS offers to install the command line developer tools, accept, then run the command again. It must show 3.9 or later.

Windows:

1. Open PowerShell as administrator (right-click PowerShell, “Run as administrator”), type `wsl --install`, then restart the computer. WSL installs Ubuntu ([Microsoft's page](https://learn.microsoft.com/en-us/windows/wsl/install), read on 17 November 2026).
2. Open “Ubuntu” from the Start menu, then create the Linux user it asks for.
3. Do everything that follows in this Ubuntu terminal, as on Linux. Claude Code is installed there with the Linux command, even if it is already installed on Windows. The Windows files are under `/mnt/c/` there, for example the downloaded zip under `/mnt/c/Users/<name>/Downloads/`.

## Installation

1. Install Claude Code if it is missing, with the official command ([Anthropic's page](https://code.claude.com/docs/en/setup), read on 17 November 2026):

   ```bash
   curl -fsSL https://claude.ai/install.sh | bash
   ```

   On macOS, Homebrew works too: `brew install --cask claude-code`. To update Claude Code: `claude update`, or `brew upgrade claude-code` with Homebrew.

2. Unzip the package you received, then go into its folder:

   ```bash
   unzip YYYY-MM-DD_claude-credit.zip
   cd claude-credit
   ```

   Without `unzip` (often the case under WSL): `python3 -m zipfile -e YYYY-MM-DD_claude-credit.zip .`. From GitHub: `git clone https://github.com/davjack/claude-credit.git`, then `cd claude-credit`.

3. Run the installation:

   ```bash
   python3 install.py
   ```

   It copies the tool into `~/.local/share/claude-credit/` and creates the command `~/.local/bin/claude-credit`. It does not ask for `sudo` and changes no Claude Code setting.

4. If the installation reports that `~/.local/bin` is not in the PATH (« n'est pas dans le PATH »), add the line it shows to `~/.bashrc` (Linux, WSL) or to `~/.zshrc` (macOS), then open a new terminal.

5. Optional: install the two packages of the full display. Without them, a simple display takes over.

   | System | Command |
   |---|---|
   | Debian, Ubuntu, WSL | `sudo apt install python3-rich python3-prompt-toolkit` |
   | Fedora | `sudo dnf install python3-rich python3-prompt-toolkit` |
   | Arch | `sudo pacman -S python-rich python-prompt_toolkit` |
   | macOS, others | `python3 -m pip install --user rich prompt_toolkit` |

   If pip refuses with “externally-managed-environment” (for example Homebrew's Python), add `--break-system-packages` to the command: the packages go into the user's folder, not the system's.

## Usage

Always from the project's folder:

| Situation | Command |
|---|---|
| Start a new session | `claude-credit` |
| Resume the folder's last session | `claude-credit -c` |
| Choose among the folder's sessions | `claude-credit -r` |
| Resume a specific session | `claude-credit -r <id>` |
| Cap the session's spending, with any of the four | `--budget <dollars>`, for example `claude-credit -c --budget 5` |
| Show the help | `claude-credit --help` |

At launch, paste the key at the “Clé :” (key) prompt, then press Enter. It is not displayed. Before the first message, `claude-credit` checks that Claude Code will indeed bill the key, and stops otherwise without sending anything.

The keys:

- Enter sends the message, even while Claude is working. Claude reads it as soon as the running tools finish, in the same turn.
- Esc or Ctrl+C interrupts the current answer without closing the session.
- `/` at the start of the input lists the session's commands, skills included.
- ↑ and Ctrl+R recall the messages already typed in this folder. A line that contains a key is never kept.
- Ctrl+O shows the full output of the last action.
- New line: `\` then Enter, Alt+Enter or Ctrl+J.
- Beyond 30 seconds of work, a desktop notification signals the end of the turn (Linux, with `notify-send`).

To quit: `exit`, Ctrl+D on an empty input, or Ctrl+C twice on an empty input. `claude-credit` then shows the session's ID and the two ways to resume it, in Claude Code and on the credit:

```
Session : <id>
Dans Claude Code : claude --resume <id>
Sur le crédit : claude-credit -r <id>
```

In the simple display, a typed line is sent at once. A text pasted over several lines forms a single message, sent with an empty line.

## What a session costs

- Resuming a session means reloading its whole context. Beyond 300,000 tokens (about $2.40 with Opus 5.5 as of 17 November 2026), `claude-credit -c` and `claude-credit -r <id>` ask for confirmation (“La reprendre quand même ? [o/N]”: answer `o` or `y` to resume it anyway). For new work, `claude-credit` alone starts a new session, much cheaper.
- `claude-credit` keeps the conversation's cache for one hour instead of five minutes: a pause to read does not make you pay for everything again.
- Once `--budget` is reached, the current turn runs to its end, then Claude takes no more messages.
- The cost shown at each turn is an estimate. The figure that counts is on the Console's [Cost](https://platform.claude.com/cost) page.

## Security

- The firewall (`hooks/bash-firewall.py`) examines each command before Claude runs it. It blocks recursive deletions (`rm -r`, `find -delete`) of the whole project or outside the project's folder (`/tmp` stays allowed), destructive Git commands (`reset --hard`, `push --force`, `clean -fdx`), a download piped into a shell (`curl … | sh`), formatting or raw writing of a disk (`mkfs`, `dd`, `shred`), most of these commands when they are wrapped (`bash -c`, `xargs`, `find -exec`), and the interactive wizards that would hang the session, including `make backup` and `make restore`. Claude receives the reason for the refusal and can look for another way.
- It does not protect everything: it examines only shell commands, and only in known forms. A plain `rm`, a `mv`, a redirection (`>`), a command placed inside `$(…)`, a script Claude runs and the files Claude writes or edits itself get through. It judges paths from the folder where the command starts, without following its `cd`s.
- It is active only during `claude-credit` sessions. Claude Code's settings (`~/.claude/settings.json`) are not changed. So that the firewall runs even if these settings or the project's turn hooks off (`disableAllHooks`), `claude-credit` turns them back on during its session: the user's and the project's other hooks then run too. Only settings imposed by the computer's administrator (managed settings) can still set the firewall aside: step 3 of the “First try” shows it. Without the firewall's files, `claude-credit` refuses to start.
- The key is visible to the commands Claude runs during the session (tests, scripts, hooks). A program of the project that reads `ANTHROPIC_API_KEY` therefore spends the credit. The terminal itself never sees it.
- On Linux, at the end of the session a guardian stops everything the session left running (a background command, a development server). Only what was handed to a service outside the session (`systemd-run`, `at`, a tmux server already running) or launched with `sudo` escapes it.

## First try

1. In a project's folder (not in your home folder), run `claude-credit`, then paste the key.
2. Send a short message, for example “Reply only OK”. The answer arrives with a cost line.
3. Ask Claude to run `rm -rf ~/firewall-test`. The firewall must refuse the command (“BLOCKED by bash-firewall”).
4. Quit with `exit`.

## Common problems

| What is displayed | What to do |
|---|---|
| “Crédit API épuisé ou pas encore arrivé” (API credit spent or not arrived yet; “Credit balance too low”) | The credit is spent or not paid in yet: see the Console's [Billing](https://platform.claude.com/settings/billing) page |
| “Clé API refusée” (API key refused) | The key is wrong or revoked: create one on the API keys page |
| “Ce n'est pas une clé API de la Console Anthropic” (not an Anthropic Console API key) | The pasted key is not an API key: it must start with `sk-ant-api` |
| “claude ne facturerait pas la clé API” (claude would not bill the API key) | Claude Code would use an authentication other than the key, for example the `ANTHROPIC_AUTH_TOKEN` variable: nothing was sent |
| “nouvel essai” (retry) | Rate limit reached: Claude Code retries on its own |
| Simple display instead of the full display | Install the two packages of step 5 of the installation |
| “claude ne peut pas être lancé” (claude cannot be started) | Claude Code is not installed or not in the PATH |
| “Pare-feu introuvable” (firewall not found) | Run `python3 install.py` again from the package's folder |

## Updating and uninstalling

- Update: unzip the new package (or `git pull` in the cloned folder), then run `python3 install.py` again.
- Uninstall: `rm -r ~/.local/share/claude-credit ~/.local/bin/claude-credit`. The history of typed messages is in `~/.local/state/claude-credit/` (or `$XDG_STATE_HOME/claude-credit/` if that variable is set).

## Roadmap

Planned:

- An English interface, with French still available.

Known limits, described in this guide:

- macOS: not tried on a Mac yet; the process guardian and the end-of-turn notification are Linux-only.
- Windows: only through WSL.
- Firewall: it does not see yet a plain `rm`, a `mv`, a redirection (`>`), a command placed inside `$(…)`, a script Claude runs, or the files Claude writes or edits itself; it judges paths from the folder where the command starts, without following its `cd`s.

## License

`claude-credit` is free software, under the GNU AGPL version 3 or later (file `LICENSE`). Anyone may use, study, modify and redistribute it. A modified version must be offered with its source code, under the same license, to those who receive it and also to those who use it remotely over a network, including, on the most direct reading of the license, over SSH on someone else's server.

Questions or problems: open an [issue](https://github.com/davjack/claude-credit/issues) on GitHub.

<!-- Version française -->

# claude-credit en français

Ce document explique comment installer et utiliser `claude-credit`, un outil libre publié par Davjack sur [GitHub](https://github.com/davjack/claude-credit). Il permet de continuer à travailler dans Claude Code en payant avec une clé API de la Console Anthropic : le crédit API mensuel d'un plan Max, Ultra ou Team, ou un compte API ordinaire.

Pour l'instant, l'installation et `claude-credit` lui-même affichent leurs messages en français. Des améliorations suivront (voir « Feuille de route »).

## Ce que fait claude-credit

Claude Code interactif ne peut pas être payé par le crédit API mensuel d'un plan. Ce crédit couvre en revanche `claude -p`, le mode non interactif de Claude Code, lancé avec une clé API ([page d'Anthropic](https://support.claude.com/en/articles/17154008-monthly-api-credits-for-max-ultra-and-team-plans), lue le 17.11.2026).

`claude-credit` garde un seul `claude -p` ouvert pendant toute la séance et lui passe les messages. Claude Code travaille comme d'habitude : mêmes sessions, mêmes hooks, mêmes skills, même `CLAUDE.md`. L'affichage ressemble à celui de Claude Code : réponse mise en forme, une ligne par action, coût à la fin de chaque tour.

Le modèle est Opus 5.5 avec un contexte d'un million de jetons. Aucune autorisation n'est demandée pendant la séance : un pare-feu bloque les commandes destructrices (voir « Sécurité »).

## Ce qu'il faut

- Un ordinateur sous Linux, macOS 13 ou plus récent, ou Windows 10 (version 2004 ou plus récente) ou 11 avec WSL.
- Python 3.9 ou plus récent (`claude-credit` a été essayé avec Python 3.9 et 3.14 sous Linux).
- Claude Code, installé et à jour.
- Sa propre clé API de la Console Anthropic, qui commence par `sk-ant-api`.

Selon le système :

- Linux : tout fonctionne.
- macOS : devrait fonctionner, sauf le gardien des processus (voir « Sécurité ») et la notification de fin de tour, propres à Linux. Non essayé sur un Mac à la date de ce guide.
- Windows : `claude-credit` tourne dans WSL, le Linux intégré à Windows, comme sous Linux. Windows seul, sans WSL, n'est pas pris en charge : `claude-credit` a besoin du terminal et de la gestion des processus d'Unix.

## Obtenir une clé API

Il y a deux cas.

Avec un plan Max, Ultra ou Team :

1. Sur claude.ai, ouvrir « Settings > Billing » (pour Team : « Organization settings > Billing »).
2. Dans la rubrique « API credits », choisir « Link organization », puis l'organisation de la Console qui recevra le crédit, ou en créer une.
3. Dans cette organisation, créer une clé sur la page [API keys](https://platform.claude.com/settings/keys) de la Console.

Le crédit est un montant mensuel fixe. Ce qui n'est pas utilisé est perdu à la fin du cycle de facturation. Une fois le crédit épuisé, les requêtes s'arrêtent jusqu'au crédit suivant, sauf si l'organisation a acheté des crédits ou activé le rechargement automatique. Rien n'est jamais facturé sur le plan.

Avec un compte API ordinaire, chaque séance se paie :

1. Créer un compte sur [platform.claude.com](https://platform.claude.com) et acheter des crédits (page « Billing »).
2. Fixer un plafond de dépense mensuel sur la page [Spend limits](https://platform.claude.com/settings/spend-limits).
3. Créer une clé sur la page [API keys](https://platform.claude.com/settings/keys).

Une nouvelle organisation commence parfois avec des limites de débit plus basses que les limites standard ([page d'Anthropic](https://platform.claude.com/docs/en/api/rate-limits), lue le 17.11.2026). Les lignes « nouvel essai » qui s'affichent alors sont Claude Code qui réessaie.

ATTENTION : ne jamais exporter `ANTHROPIC_API_KEY` dans le terminal ni dans `~/.bashrc` ou `~/.zshrc`. Claude Code interactif utiliserait alors la clé au lieu de l'abonnement, une fois la clé acceptée ([page d'Anthropic](https://code.claude.com/docs/en/authentication), lue le 17.11.2026). `claude-credit` demande la clé à chaque lancement et ne la donne qu'à son propre `claude`.

## Préparer son système

Linux : vérifier que `python3 --version` affiche 3.9 ou plus.

macOS :

1. Ouvrir le Terminal (Applications > Utilitaires > Terminal).
2. Taper `python3 --version`. Si macOS propose d'installer les outils de développement en ligne de commande, accepter, puis relancer la commande. Elle doit afficher 3.9 ou plus.

Windows :

1. Ouvrir PowerShell en administrateur (clic droit sur PowerShell, « Exécuter en tant qu'administrateur »), taper `wsl --install`, puis redémarrer l'ordinateur. WSL installe Ubuntu ([page de Microsoft](https://learn.microsoft.com/en-us/windows/wsl/install), lue le 17.11.2026).
2. Ouvrir « Ubuntu » depuis le menu Démarrer, puis créer l'utilisateur Linux demandé.
3. Faire toute la suite dans ce terminal Ubuntu, comme sous Linux. Claude Code s'y installe avec la commande Linux, même s'il est déjà installé sous Windows. Les fichiers de Windows s'y trouvent sous `/mnt/c/`, par exemple le zip téléchargé sous `/mnt/c/Users/<nom>/Downloads/`.

## Installation

1. Installer Claude Code s'il manque, par la commande officielle ([page d'Anthropic](https://code.claude.com/docs/en/setup), lue le 17.11.2026) :

   ```bash
   curl -fsSL https://claude.ai/install.sh | bash
   ```

   Sur macOS, Homebrew convient aussi : `brew install --cask claude-code`. Pour mettre Claude Code à jour : `claude update`, ou `brew upgrade claude-code` avec Homebrew.

2. Décompresser le paquet reçu, puis entrer dans son dossier :

   ```bash
   unzip AAAA-MM-JJ_claude-credit.zip
   cd claude-credit
   ```

   Sans `unzip` (souvent le cas sous WSL) : `python3 -m zipfile -e AAAA-MM-JJ_claude-credit.zip .`. Depuis GitHub : `git clone https://github.com/davjack/claude-credit.git`, puis `cd claude-credit`.

3. Lancer l'installation :

   ```bash
   python3 install.py
   ```

   Elle copie l'outil dans `~/.local/share/claude-credit/` et crée la commande `~/.local/bin/claude-credit`. Elle ne demande pas `sudo` et ne modifie aucun réglage de Claude Code.

4. Si l'installation signale que `~/.local/bin` n'est pas dans le PATH, ajouter la ligne qu'elle affiche à `~/.bashrc` (Linux, WSL) ou à `~/.zshrc` (macOS), puis ouvrir un nouveau terminal.

5. Facultatif : installer les deux paquets de l'affichage complet. Sans eux, un affichage simple prend le relais.

   | Système | Commande |
   |---|---|
   | Debian, Ubuntu, WSL | `sudo apt install python3-rich python3-prompt-toolkit` |
   | Fedora | `sudo dnf install python3-rich python3-prompt-toolkit` |
   | Arch | `sudo pacman -S python-rich python-prompt_toolkit` |
   | macOS, autres | `python3 -m pip install --user rich prompt_toolkit` |

   Si pip refuse avec « externally-managed-environment » (par exemple le Python de Homebrew), ajouter `--break-system-packages` à la commande : les paquets vont dans le dossier de l'utilisateur, pas dans celui du système.

## Utilisation

Toujours depuis le dossier du projet :

| Situation | Commande |
|---|---|
| Démarrer une session neuve | `claude-credit` |
| Reprendre la dernière session du dossier | `claude-credit -c` |
| Choisir parmi les sessions du dossier | `claude-credit -r` |
| Reprendre une session précise | `claude-credit -r <numéro>` |
| Plafonner la dépense de la séance, avec chacune des quatre | `--budget <dollars>`, par exemple `claude-credit -c --budget 5` |
| Afficher l'aide | `claude-credit --help` |

Au lancement, coller la clé, puis Entrée. Elle ne s'affiche pas. Avant le premier message, `claude-credit` vérifie que Claude Code facturera bien la clé, et s'arrête sinon sans rien envoyer.

Les touches :

- Entrée envoie le message, même pendant que Claude travaille. Claude le lit dès la fin des outils en cours, dans le même tour.
- Échap ou Ctrl+C interrompt la réponse en cours sans fermer la session.
- `/` en début de saisie liste les commandes de la session, skills comprises.
- ↑ et Ctrl+R rappellent les messages déjà tapés dans ce dossier. Une ligne qui contient une clé n'est jamais gardée.
- Ctrl+O affiche en entier la sortie de la dernière action.
- Retour à la ligne : `\` puis Entrée, Alt+Entrée ou Ctrl+J.
- Au-delà de 30 secondes de travail, une notification du bureau signale la fin du tour (Linux, avec `notify-send`).

Pour quitter : `exit`, Ctrl+D sur une saisie vide, ou Ctrl+C deux fois sur une saisie vide. `claude-credit` affiche alors le numéro de la session et les deux façons de la reprendre :

```
Session : <numéro>
Dans Claude Code : claude --resume <numéro>
Sur le crédit : claude-credit -r <numéro>
```

Dans l'affichage simple, une ligne tapée part aussitôt. Un texte collé sur plusieurs lignes forme un seul message, envoyé par une ligne vide.

## Ce que coûte une séance

- Reprendre une session oblige à recharger tout son contexte. Au-delà de 300'000 jetons (environ 2.40 $ avec Opus 5.5 au 17.11.2026), `claude-credit -c` et `claude-credit -r <numéro>` demandent confirmation. Pour un travail nouveau, `claude-credit` seul démarre une session neuve, bien moins chère.
- `claude-credit` garde le cache de la conversation une heure au lieu de cinq minutes : une pause de lecture ne fait pas tout repayer.
- Une fois `--budget` atteint, le tour en cours va au bout, puis Claude ne prend plus de message.
- Le coût affiché à chaque tour est une estimation. Le chiffre qui fait foi est sur la page [Cost](https://platform.claude.com/cost) de la Console.

## Sécurité

- Le pare-feu (`hooks/bash-firewall.py`) examine chaque commande avant que Claude la lance. Il bloque les suppressions récursives (`rm -r`, `find -delete`) du projet entier ou hors du dossier du projet (`/tmp` reste permis), les commandes Git destructrices (`reset --hard`, `push --force`, `clean -fdx`), un téléchargement passé à un shell par un tube (`curl … | sh`), le formatage ou l'écriture brute d'un disque (`mkfs`, `dd`, `shred`), la plupart de ces commandes quand elles sont enveloppées (`bash -c`, `xargs`, `find -exec`), et les assistants interactifs qui bloqueraient la séance, dont `make backup` et `make restore`. Claude reçoit la raison du refus et peut chercher une autre voie.
- Il ne protège pas tout : il n'examine que les commandes shell, et seulement sous des formes connues. Un `rm` simple, un `mv`, une redirection (`>`), une commande placée dans `$(…)`, un script lancé par Claude et les fichiers que Claude écrit ou modifie lui-même passent. Il juge les chemins depuis le dossier où la commande démarre, sans suivre ses `cd`.
- Il n'est actif que pendant les séances `claude-credit`. Les réglages de Claude Code (`~/.claude/settings.json`) ne sont pas modifiés. Pour que le pare-feu tourne même si ces réglages ou ceux du projet coupent les hooks (`disableAllHooks`), `claude-credit` les réactive pendant sa séance : les autres hooks de l'utilisateur et du projet tournent alors aussi. Seuls des réglages imposés par l'administrateur de l'ordinateur (managed settings) peuvent encore écarter le pare-feu : l'étape 3 du « Premier essai » le montre. Sans les fichiers du pare-feu, `claude-credit` refuse de démarrer.
- La clé est visible des commandes que Claude lance pendant la séance (tests, scripts, hooks). Un programme du projet qui lit `ANTHROPIC_API_KEY` dépense donc le crédit. Le terminal, lui, ne la voit jamais.
- Sous Linux, un gardien arrête à la fin de la séance tout ce qu'elle a laissé tourner (une commande en tâche de fond, un serveur de développement). Seul ce qui a été confié à un service hors de la séance (`systemd-run`, `at`, un serveur tmux déjà lancé) ou lancé par `sudo` lui échappe.

## Premier essai

1. Dans le dossier d'un projet (pas dans le dossier personnel), lancer `claude-credit`, puis coller la clé.
2. Envoyer un message court, par exemple « Réponds seulement OK ». La réponse arrive avec une ligne de coût.
3. Demander à Claude de lancer `rm -rf ~/essai-pare-feu`. La commande doit être refusée par le pare-feu (« BLOCKED by bash-firewall »).
4. Quitter avec `exit`.

## Problèmes fréquents

| Ce qui s'affiche | Ce qu'il faut faire |
|---|---|
| « Crédit API épuisé ou pas encore arrivé » (« Credit balance too low ») | Le crédit est épuisé ou pas encore versé : voir la page [Billing](https://platform.claude.com/settings/billing) de la Console |
| « Clé API refusée » | La clé est fausse ou révoquée : en créer une sur la page API keys |
| « Ce n'est pas une clé API de la Console Anthropic » | La clé collée n'est pas une clé API : elle doit commencer par `sk-ant-api` |
| « claude ne facturerait pas la clé API » | Claude Code utiliserait une autre authentification que la clé, par exemple la variable `ANTHROPIC_AUTH_TOKEN` : rien n'a été envoyé |
| « nouvel essai » | Limite de débit atteinte : Claude Code réessaie seul |
| Affichage simple au lieu de l'affichage complet | Installer les deux paquets de l'étape 5 de l'installation |
| « claude ne peut pas être lancé » | Claude Code n'est pas installé ou pas dans le PATH |
| « Pare-feu introuvable » | Relancer `python3 install.py` depuis le dossier du paquet |

## Mise à jour et désinstallation

- Mettre à jour : décompresser le nouveau paquet (ou `git pull` dans le dossier cloné), puis relancer `python3 install.py`.
- Désinstaller : `rm -r ~/.local/share/claude-credit ~/.local/bin/claude-credit`. L'historique des messages tapés se trouve dans `~/.local/state/claude-credit/` (ou `$XDG_STATE_HOME/claude-credit/` si cette variable est définie).

## Feuille de route

Prévu :

- Une interface en anglais, le français restant disponible.

Limites connues, décrites dans ce guide :

- macOS : pas encore essayé sur un Mac ; le gardien des processus et la notification de fin de tour sont propres à Linux.
- Windows : seulement par WSL.
- Pare-feu : il ne voit pas encore un `rm` simple, un `mv`, une redirection (`>`), une commande placée dans `$(…)`, un script lancé par Claude, ni les fichiers que Claude écrit ou modifie lui-même ; il juge les chemins depuis le dossier où la commande démarre, sans suivre ses `cd`.

## Licence

`claude-credit` est un logiciel libre, sous licence GNU AGPL version 3 ou ultérieure (fichier `LICENSE`). Chacun peut l'utiliser, l'étudier, le modifier et le redistribuer. Une version modifiée doit être proposée avec ses sources, sous la même licence, à ceux qui la reçoivent et aussi à ceux qui l'utilisent à distance par un réseau, y compris, selon la lecture la plus directe de la licence, par SSH sur le serveur de quelqu'un d'autre.

Une question, un problème : ouvrir une [issue](https://github.com/davjack/claude-credit/issues) sur GitHub.
