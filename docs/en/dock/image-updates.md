# Image updates

[Deutsch](../../dock/image-updates.md) · [Dock documentation](README.md) · [Troubleshooting](troubleshooting.md)

## Checking and applying are separate

On **Docker**, **Image-Updates prüfen** (check image updates) starts a read-only comparison: does the registry have
a different digest under the same image tag than the running container? It downloads no image and restarts no container.
The check does not search for higher version numbers. To move from `app:1` to `app:2`, change the Compose file,
in the repository for a Git stack.

Enable automatic checking under **Einstellungen → Erweiterungen → Docker → Erweitert** (Settings → Extensions → Docker
→ Advanced) with `image_updates_enabled`; it is off by default. It does not install updates.
The Docker host queries the registry; for private images, store registry credentials and approve the host login.
An image that cannot be checked is not necessarily up to date.

## Update one service

1. Select a container with an available update and open its update plan.
2. Review the service, Compose project, affected containers and warnings. Dock gives a reason when updating is unavailable.
3. Confirm and approve applying the update. Dock saves the old image under a local tag, downloads the new image
   and recreates the Compose service. The service is briefly unavailable.
4. Check the result under **Aktionen** (actions), the container state and application logs.

Supported targets are suitable Compose services with accessible Compose files on the host.
Individual `docker run` containers must first be converted into a stack; the interface provides a separate action for that.
Digest-pinned images, local builds and unclear or changed Compose mappings cannot be updated like a normal registry
image under the same tag. Dock shows the specific reason.

## Stack updates and backups

Under **Stacks**, open the update plan for the whole stack. The preview lists services that can be updated.

Stopped services are not updated here because doing so would start them.
To start them with the current image, first choose **Images holen** (pull images) for the stack, then **Anwenden** (apply).
**Anwenden** alone can reuse the old local image when the tag has not changed.

Optionally choose **Volumes vorher sichern** (back up volumes first). Dock checks space and stops affected services
during the backup. Compose then applies the stack. Archives live on the Docker host, outside Dock's data volume.

Restore or delete stack backups under **Stacks → Sicherungen** (backups).
Bind mounts and data outside the backed-up volumes need their own backups.
A file or image rollback does not restore database contents.

## Rollback and limits

After a stack update, **Zurück zur vorigen Version** (return to the previous version) offers rollback using saved images.
For an individual image update, Dock attempts automatic rollback by default if the new container does not start
cleanly. `image_update_auto_rollback` disables that attempt. Detected databases and other data stores are excluded
from automatic rollback because a newer version may have changed the data format.
Detection does not replace your assessment of the application.

Keep saved images on the host for rollback. Even a container detected as running or healthy may work incorrectly;
check the application itself after updating.
Update Dock itself through [Compose](../../../deploy/nodvard-dock/README.en.md#updating-dock).

Code references: [digest checks](../../../extensions/docker/src/nodvard_deck_ext_docker/image_updates.py),
[suitable Compose services](../../../extensions/docker/src/nodvard_deck_ext_docker/image_apply.py),
[applying and automatic rollback](../../../extensions/docker/src/nodvard_deck_ext_docker/applier.py),
[stack updates](../../../extensions/docker/src/nodvard_deck_ext_docker/stack_update.py),
[volume restore](../../../extensions/docker/src/nodvard_deck_ext_docker/stack_restore.py).
