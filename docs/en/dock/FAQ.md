# Frequently Asked Questions: Nodvard Dock

[Deutsch](../../dock/FAQ.md) · [Dock documentation](README.md) · [Dock quick start](../../../deploy/nodvard-dock/README.en.md#getting-started)

Self-hosted, source available (PolyForm Noncommercial), free for personal use.
The following 12 FAQ entries cover Docker functionality in Nodvard Dock and Deck. All features and limits are verified directly against the source code.

## 1. Docker networks: creating a new network and internal networks

**Question:** How do you create a new Docker network and what does the "internal only" option do?  
**Answer:** You create a network using the "Netz anlegen" button and assign a name of up to 63 characters ([NetworkTools.tsx:14, 56-62](../../../extensions/docker/frontend/src/NetworkTools.tsx)). If you check the internal option, connected containers reach each other but cannot access the internet or your local network through this network ([NetworkTools.tsx:68-70](../../../extensions/docker/frontend/src/NetworkTools.tsx)). You cannot use reserved names like bridge, host, none, or default for your custom networks ([NetworkTools.tsx:15, 65](../../../extensions/docker/frontend/src/NetworkTools.tsx)).

## 2. Docker networks: manually connecting containers from Compose stacks

**Question:** What happens when you manually connect a container from a Compose stack to a network?  
**Answer:** You can connect or disconnect containers directly within the network tools view ([NetworkTools.tsx:107-124](../../../extensions/docker/frontend/src/NetworkTools.tsx)). If the container belongs to a Compose stack, this manual connection only lasts until the next stack modification or update ([NetworkTools.tsx:80-85](../../../extensions/docker/frontend/src/NetworkTools.tsx)). As soon as Compose recreates the container, it will only connect to the networks defined in the compose file ([NetworkTools.tsx:82-85](../../../extensions/docker/frontend/src/NetworkTools.tsx)).

## 3. Docker networks: disconnecting the last network and self-protection

**Question:** Why does Dock warn before disconnecting the last network, and can you disconnect Dock itself?  
**Answer:** If you disconnect a container from its only network, it loses all connectivity, has no internet access, and its published ports stop functioning ([NetworkTools.tsx:111-113](../../../extensions/docker/frontend/src/NetworkTools.tsx)). For this reason, Dock displays an explicit confirmation warning before executing the disconnection ([NetworkTools.tsx:114-116](../../../extensions/docker/frontend/src/NetworkTools.tsx)). Nodvard's own container remains permanently linked to protect the management connection and cannot be disconnected in the interface ([NetworkTools.tsx:105, 141-148](../../../extensions/docker/frontend/src/NetworkTools.tsx)).

## 4. Pulling images: impact on running containers

**Question:** What does the "Image holen" feature do, and what happens to your running containers?  
**Answer:** Using "Image holen", Docker downloads a specific container image from its registry to your server ([ImagePull.tsx:1, 41](../../../extensions/docker/frontend/src/ImagePull.tsx)). Your running containers remain completely unchanged and continue running without interruption ([ImagePull.tsx:77-80](../../../extensions/docker/frontend/src/ImagePull.tsx)). You may close or navigate away from the page while the download executes as an approved action in the background ([ImagePull.tsx:41, 77-80](../../../extensions/docker/frontend/src/ImagePull.tsx)).

## 5. Pulling images: version tags versus latest

**Question:** Why should you specify a fixed tag instead of "latest" when pulling an image?  
**Answer:** If you omit both tag and digest from the input, Docker automatically pulls the latest tag corresponding to whatever is current in the registry ([ImagePull.tsx:21-25](../../../extensions/docker/frontend/src/ImagePull.tsx)). For stacks, Dock recommends specifying a pinned version like nginx:1.27 to ensure your environment remains reproducible and predictable ([ImagePull.tsx:71-75](../../../extensions/docker/frontend/src/ImagePull.tsx)). Pinned versions safeguard you from future container startups inadvertently pulling unintended updates ([ImagePull.tsx:71-75](../../../extensions/docker/frontend/src/ImagePull.tsx)).

## 6. Pulling images: updating stopped services in stacks

**Question:** How do you start stopped services of a stack using a newly pulled image?  
**Answer:** A regular stack update plan skips stopped services so they do not inadvertently start during the rollout ([image-updates.md:34-35](image-updates.md#stack-updates-and-backups)). To start them with the new image, choose "Images holen" on the stack first and then click "Anwenden" ([image-updates.md:35-36](image-updates.md#stack-updates-and-backups)). Running "Anwenden" alone might otherwise continue using the old local image if the image tag has not changed ([image-updates.md:36](image-updates.md#stack-updates-and-backups)).

## 7. Custom templates: what is saved and exclusion of passwords

**Question:** What does Dock store in custom templates and why are fixed passwords rejected?  
**Answer:** A custom template only stores the raw Compose file for future stacks, never an accompanying .env file ([OwnTemplates.tsx:1, 15-17](../../../extensions/docker/frontend/src/OwnTemplates.tsx)). Dock rejects recognized hardcoded passwords, tokens, and secret keys upon saving to prevent sensitive secrets from being shared ([OwnTemplates.tsx:16-17](../../../extensions/docker/frontend/src/OwnTemplates.tsx)). Saved custom templates become visible to everyone permitted to deploy stacks across your hosts ([OwnTemplates.tsx:17](../../../extensions/docker/frontend/src/OwnTemplates.tsx)).

## 8. Custom templates: declaring mandatory variables with question marks

**Question:** How do you define mandatory environment variables in your custom Compose templates?  
**Answer:** Declare required variables in the Compose file with `${NAME:?}` or `${NAME?}`. Fill in the recognised fields under **Neuer Stack → Werte für diesen Stack** (new stack, values for this stack) before checking or creating. After approval, Dock writes the values to the new stack's `.env` and never stores them in a template. Names containing `PASSWORD`, `SECRET`, `TOKEN` or `KEY` are masked. Variables without defaults such as `${NAME}` also get fields, but may remain empty. `$$NAME` is evaluated later by the container. See [Values for this stack](templates.md#values-for-this-stack) for examples and details.

## 9. Custom templates: subsequent catalog changes

**Question:** Do subsequent template updates change your previously created stacks?  
**Answer:** No, any stack created from a template remains an independent local Compose file on your host ([templates.md:41-42](templates.md#edition-and-maintenance)). Subsequent modifications to the built-in catalog or your custom templates never alter existing stacks ([templates.md:41-42](templates.md#edition-and-maintenance)). Furthermore, Dock never overwrites an existing stack directory on the target host when creating new applications ([templates.md:32](templates.md#use-a-template)).

## 10. Stacks from Git: protocols and Git on the host

**Question:** Which protocols does Dock support for stacks from Git, and must Git run on the host?  
**Answer:** Dock connects to repositories strictly using HTTPS clone URLs; Git over SSH is not supported ([stacks-from-git.md:25-26](stacks-from-git.md#enter-the-source)). You do not need Git installed on your Docker host because Dock handles fetching repositories over HTTPS itself ([stacks-from-git.md:35-36](stacks-from-git.md#enter-the-source)). For private repositories, you store an access token securely in the vault under the advanced Docker settings ([stacks-from-git.md:30-33](stacks-from-git.md#enter-the-source)).

## 11. Stacks from Git: preserving local .env files

**Question:** Are your local environment files like .env overwritten when deploying updates from Git?  
**Answer:** No, existing .env files and known stack environment files on the host are never replaced during a Git deployment ([stacks-from-git.md:41-42](stacks-from-git.md#review-and-apply)). Dock synchronizes only the Compose file and accompanying files inside the specified directory ([stacks-from-git.md:28-29, 41-42](stacks-from-git.md#enter-the-source)). Your secret keys and host-specific variables remain safely preserved directly on your Docker host ([stacks-from-git.md:21-22, 41-42](stacks-from-git.md#enter-the-source)).

## 12. Stacks from Git: limits and file restrictions

**Question:** What limits and file restrictions apply when syncing stacks from Git?  
**Answer:** The synchronized folder may contain at most 300 files and 10 MiB of total file content, with the Compose file limited to 256 KiB ([stacks-from-git.md:64](stacks-from-git.md#new-commits-and-limits)). Submodules, symlinks, and Git LFS files are unsupported and will prevent deployment ([stacks-from-git.md:61-62](stacks-from-git.md#new-commits-and-limits)). In addition, the stack must consist of exactly one Compose file placed directly in the root of the stack folder ([stacks-from-git.md:60](stacks-from-git.md#new-commits-and-limits)).
