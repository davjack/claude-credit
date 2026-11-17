# claude-credit

Ce document explique comment installer et utiliser `claude-credit`, un outil libre publié par Davjack sur [GitHub](https://github.com/davjack/claude-credit). Il permet de continuer à travailler dans Claude Code en payant avec une clé API de la Console Anthropic : le crédit API mensuel d'un plan Max, Ultra ou Team, ou un compte API ordinaire.

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

## Licence

`claude-credit` est un logiciel libre, sous licence GNU AGPL version 3 ou ultérieure (fichier `LICENSE`). Chacun peut l'utiliser, l'étudier, le modifier et le redistribuer. Une version modifiée doit être proposée avec ses sources, sous la même licence, à ceux qui la reçoivent et aussi à ceux qui l'utilisent à distance par un réseau, y compris, selon la lecture la plus directe de la licence, par SSH sur le serveur de quelqu'un d'autre.

Une question, un problème : ouvrir une [issue](https://github.com/davjack/claude-credit/issues) sur GitHub.
