import shlex
import shutil
import subprocess
import sys

import pytest
from shield_helper_kit import SH, helper_copy, stubs, wait_for

pytestmark = pytest.mark.skipif(sys.platform == "win32" or not shutil.which("sh"), reason="braucht eine POSIX-Shell")
TOKEN = "a" * 32


@pytest.fixture
def server(tmp_path):
    conf = tmp_path / "etc/ssh/sshd_config.d/nodvard-haertung.conf"
    home = tmp_path / "home"
    home.mkdir()
    log = tmp_path / "calls"
    tools = {
        "sshd": f'''echo "sshd $*" >> '{log}'
if [ ! -f '{tmp_path}/silent' ]; then
    echo 'debug2: load_server_config: filename sshd_config' >&2
fi
if [ -f '{tmp_path}/match-block' ]; then
    echo "debug3: checking syntax for 'Match User legacy' on line 1" >&2
fi
if [ "$1" = -t ]; then
    [ ! -f '{tmp_path}/invalid' ] || [ ! -f '{conf}' ] || exit 1
    [ ! -f '{tmp_path}/invalid-without-conf' ] || [ -f '{conf}' ]
elif [ -f '{tmp_path}/ignored' ]; then
    echo 'passwordauthentication yes'
elif [ -f '{conf}' ]; then
    tr 'A-Z' 'a-z' < '{conf}' | if [ -f '{tmp_path}/report-filter' ]; then sed -f '{tmp_path}/report-filter'; else cat; fi
else
    echo 'passwordauthentication yes'
fi
''',
        "systemctl": f'''echo "reload $*" >> '{log}'
[ ! -f '{tmp_path}/reload-fails' ]
''',
        "nohup": f'''echo watchdog >> '{log}'
touch '{tmp_path}/watchdog'
if [ -f '{tmp_path}/automatic-rollback' ]; then
    timeout 5 "$@"
    printf '%s\\n' "$?" > '{tmp_path}/done'
fi
''',
        "getent": f"""if [ "$2" = root ] && [ -d '{tmp_path}/roothome' ]; then
    echo 'root:x:0:0::{tmp_path}/roothome:/bin/sh'
else
    echo 'deck:x:1000:1000::{home}:/bin/sh'
fi
""",
        "runuser": 'shift 3\nexec "$@"\n',
        "ssh-keygen": "exit 0\n",
        "sleep": "exit 0\n",
        "rm": f'''for file do
    if [ "$file" = '{conf}' ] && [ -f '{tmp_path}/delete-fails' ]; then
        echo 'cannot remove configuration' >&2
        exit 1
    fi
done
exec /bin/rm "$@"
''',
    }
    bin_dir = stubs(tmp_path, tools)
    path = f"{bin_dir}:/usr/bin:/bin"
    sd = tmp_path / "sd"
    sd.mkdir()
    helper = helper_copy(tmp_path, path=path, sdmark=sd)

    def run(op, *args, user="deck", sudo_user=None):
        env = {"PATH": path, "LC_ALL": "C"}
        if sudo_user: env["SUDO_USER"] = sudo_user
        return subprocess.run([SH, str(helper), "ssh-hardening", op, user, *args], env=env,
                              capture_output=True, text=True, timeout=10, check=False)

    return run, conf, home, log


@pytest.mark.parametrize("user,root", [("deck", "no"), ("root", "prohibit-password")])
def test_apply_validate_reload_confirm_and_off(server, user, root):
    run, conf, _, log = server
    result = run("apply", TOKEN, user=user)
    assert result.returncode == 0, result.stderr
    wait_for(log.parent / "watchdog")
    pending = run("status", user=user)
    assert "@@hardened=no" in pending.stdout and "@@managed=yes" in pending.stdout
    assert conf.read_text() == f"PasswordAuthentication no\nPermitRootLogin {root}\nKbdInteractiveAuthentication no\nMaxAuthTries 3\n"
    lines = log.read_text().splitlines()
    assert lines.index("sshd -t") < next(i for i, line in enumerate(lines) if line.startswith("reload "))
    assert lines.count("sshd -t") == 2
    assert "watchdog" in lines
    assert run("confirm", TOKEN, user=user).returncode == 0
    assert "@@hardened=yes" in run("status", user=user).stdout
    assert run("expire", TOKEN, user=user).returncode == 0
    assert conf.exists()
    assert run("off", user=user).returncode == 0
    assert not conf.exists()
    assert "@@hardened=no" in run("status", user=user).stdout


@pytest.mark.parametrize("failure", ["invalid", "ignored", "reload-fails"])
def test_apply_failure_preserves_foreign_file(server, tmp_path, failure):
    run, conf, _, log = server
    other = tmp_path / "etc/ssh/sshd_config"
    other.parent.mkdir(parents=True)
    other.write_text("# bleibt\n")
    (tmp_path / failure).touch()
    result = run("apply", TOKEN)
    assert result.returncode != 0
    assert "shield-root:" in result.stderr, "ein Fehlschlag nennt den Grund"
    pending = tmp_path / "run/nodvard-shield-ssh/pending"
    if failure == "reload-fails":
        assert conf.exists()
        assert pending.read_text().strip() == TOKEN
        status = run("status")
        assert "@@managed=yes" in status.stdout
        assert "@@hardened=no" in status.stdout
    else:
        assert not conf.exists()
        assert not pending.exists()
    assert other.read_text() == "# bleibt\n"
    if failure == "invalid":
        assert log.read_text().splitlines().count("sshd -t") == 3


@pytest.mark.parametrize("failure", ["reload-fails", "invalid-without-conf"])
def test_failed_off_restores_confirmed_change(server, tmp_path, failure):
    run, conf, _, _ = server
    assert run("apply", TOKEN).returncode == 0
    assert run("confirm", TOKEN).returncode == 0
    original = conf.read_text()
    (tmp_path / failure).touch()
    result = run("off")
    assert result.returncode != 0
    assert "@@hardened=no" not in result.stdout
    assert conf.read_text() == original
    status = run("status")
    assert "@@managed=yes" in status.stdout
    assert "@@hardened=yes" in status.stdout
    assert not (tmp_path / "run/nodvard-shield-ssh/pending").exists()
    (tmp_path / failure).unlink()
    assert run("off").returncode == 0
    assert not conf.exists()
    status = run("status")
    assert "@@hardened=no" in status.stdout
    assert "@@managed=yes" not in status.stdout


@pytest.mark.parametrize("op", ["off", "rollback", "expire"])
def test_failed_removal_keeps_pending_change(server, tmp_path, op):
    run, conf, _, log = server
    assert run("apply", TOKEN).returncode == 0
    wait_for(tmp_path / "watchdog")
    calls = log.read_text()
    (tmp_path / "delete-fails").touch()
    result = run(op, *([] if op == "off" else [TOKEN]))
    assert result.returncode != 0
    assert "@@hardened=no" not in result.stdout
    assert "@@rollback=yes" not in result.stdout
    assert conf.exists()
    assert (tmp_path / "run/nodvard-shield-ssh/pending").read_text().strip() == TOKEN
    assert log.read_text() == calls


def test_failed_apply_cleanup_keeps_pending_change(server, tmp_path):
    run, conf, _, log = server
    (tmp_path / "ignored").touch()
    (tmp_path / "delete-fails").touch()
    result = run("apply", TOKEN)
    assert result.returncode != 0
    assert "@@pending=yes" not in result.stdout
    assert conf.exists()
    assert (tmp_path / "run/nodvard-shield-ssh/pending").read_text().strip() == TOKEN
    assert log.read_text().splitlines().count("sshd -t") == 2


def test_match_block_prevents_confirmation(server, tmp_path):
    run, conf, _, _ = server
    assert run("apply", TOKEN).returncode == 0
    (tmp_path / "match-block").touch()
    result = run("confirm", TOKEN)
    assert result.returncode != 0
    assert "@@hardened=yes" not in result.stdout
    assert "@@hardened=no" in run("status").stdout
    assert conf.exists()
    assert (tmp_path / "run/nodvard-shield-ssh/pending").read_text().strip() == TOKEN


def test_missing_debug_output_prevents_confirmation(server, tmp_path):
    run, _, _, _ = server
    assert run("apply", TOKEN).returncode == 0
    (tmp_path / "silent").touch()
    assert run("confirm", TOKEN).returncode != 0
    assert "@@hardened=no" in run("status").stdout


@pytest.fixture
def openssh_server(server, tmp_path):
    sshd = shutil.which("sshd")
    keygen = shutil.which("ssh-keygen")
    if not sshd or not keygen:
        pytest.skip("braucht OpenSSH-Server und ssh-keygen")
    run, conf, _, _ = server
    key = tmp_path / "host-key"
    subprocess.run([keygen, "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True,
                   capture_output=True, text=True, timeout=10)
    config = tmp_path / "etc/ssh/sshd_config"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(f"HostKey {key}\nUsePAM no\nInclude {conf.parent}/*.conf\n")
    check = subprocess.run([sshd, "-T", "-f", str(config)], check=False, capture_output=True, text=True, timeout=10)
    if "Missing privilege separation directory" in check.stderr:
        pytest.skip(check.stderr.strip())
    assert check.returncode == 0, check.stderr
    stubs(tmp_path, {"sshd": f'exec {shlex.quote(sshd)} -f {shlex.quote(str(config))} "$@"\n'})
    return run, conf, config, sshd


def test_openssh_global_hardening_can_be_confirmed(openssh_server):
    run, conf, _, _ = openssh_server
    assert run("apply", TOKEN).returncode == 0
    assert run("confirm", TOKEN).returncode == 0
    assert "@@hardened=yes" in run("status").stdout
    assert conf.exists()


@pytest.mark.parametrize("operation", ["apply", "confirm", "status"])
@pytest.mark.parametrize("conditional", [
    "mAtCh\tuSeR legacy\npAsSwOrDaUtHeNtIcAtIoN yes\n",
    "Match Address 192.0.2.0/24\nKbdInteractiveAuthentication yes\n",
])
def test_password_match_is_not_reported_as_hardened(openssh_server, tmp_path, operation, conditional):
    run, conf, config, sshd = openssh_server
    conf.parent.mkdir(parents=True, exist_ok=True)
    conf.write_text("PasswordAuthentication no\nPermitRootLogin no\nKbdInteractiveAuthentication no\nMaxAuthTries 3\n")
    included = tmp_path / "conditional.conf"
    included.write_text(conditional)
    with config.open("a") as output:
        output.write(f"Include {included}\n")
    global_state = subprocess.run([sshd, "-T", "-f", str(config)], check=True,
                                  capture_output=True, text=True, timeout=10).stdout
    matched_state = subprocess.run([sshd, "-T", "-f", str(config), "-C", "user=legacy,addr=192.0.2.1,host=example"],
                                   check=True, capture_output=True, text=True, timeout=10).stdout
    setting = conditional.splitlines()[1].split()[0].lower()
    assert f"{setting} no\n" in global_state
    assert f"{setting} yes\n" in matched_state
    if operation == "apply":
        conf.unlink()
    elif operation == "confirm":
        state = tmp_path / "run/nodvard-shield-ssh"
        state.mkdir()
        (state / "pending").write_text(TOKEN + "\n")
    result = run(operation, *([] if operation == "status" else [TOKEN]))
    assert "@@hardened=yes" not in result.stdout
    if operation == "status":
        assert "@@hardened=no" in result.stdout
    else:
        assert result.returncode != 0


@pytest.mark.parametrize("op", ["rollback", "expire"])
def test_unconfirmed_change_rolls_back(server, op):
    run, conf, _, log = server
    assert run("apply", TOKEN).returncode == 0
    assert run(op, TOKEN).returncode == 0
    assert not conf.exists()
    assert run("confirm", TOKEN).returncode != 0
    assert sum(line.startswith("reload") for line in log.read_text().splitlines()) == 2


def test_watchdog_rolls_back_without_confirmation(server, tmp_path):
    run, conf, _, log = server
    waiting = tmp_path / "waiting"
    release = tmp_path / "release"
    done = tmp_path / "done"
    (tmp_path / "automatic-rollback").touch()
    stubs(tmp_path, {
        "sleep": f'''printf '%s\\n' "$*" > '{waiting}'
while [ ! -f '{release}' ]; do /bin/sleep 0.05; done
''',
    })
    assert run("apply", TOKEN).returncode == 0
    wait_for(waiting, timeout_s=3)
    assert waiting.read_text().strip() == "60"
    pending = tmp_path / "run/nodvard-shield-ssh/pending"
    assert conf.exists() and pending.read_text().strip() == TOKEN
    release.touch()
    wait_for(done, timeout_s=7)
    assert done.read_text().strip() == "0"
    assert not conf.exists()
    assert not pending.exists()
    assert sum(line.startswith("reload") for line in log.read_text().splitlines()) == 2


def test_old_timer_cannot_remove_new_change(server):
    run, conf, _, _ = server
    assert run("apply", TOKEN).returncode == 0
    assert run("off").returncode == 0
    assert run("apply", "b" * 32).returncode == 0
    assert run("expire", TOKEN).returncode == 0
    assert conf.exists()
    assert run("confirm", "b" * 32).returncode == 0


@pytest.mark.parametrize("newer", [False, True])
def test_rollback_without_matching_pending_is_not_confirmed(server, newer):
    run, conf, _, log = server
    assert run("apply", TOKEN).returncode == 0
    assert run("confirm", TOKEN).returncode == 0
    if newer:
        assert run("off").returncode == 0
        assert run("apply", "b" * 32).returncode == 0
    reloads = sum(line.startswith("reload") for line in log.read_text().splitlines())
    result = run("rollback", TOKEN)
    assert result.returncode != 0
    assert "@@rollback=yes" not in result.stdout
    assert conf.exists()
    assert run("expire", TOKEN).returncode == 0
    assert conf.exists()
    assert sum(line.startswith("reload") for line in log.read_text().splitlines()) == reloads
    if newer:
        assert run("confirm", "b" * 32).returncode == 0


def test_repeated_rollback_is_confirmed_when_file_is_gone(server):
    run, conf, _, log = server
    assert run("apply", TOKEN).returncode == 0
    assert run("rollback", TOKEN).returncode == 0
    result = run("rollback", TOKEN)
    assert result.returncode == 0
    assert "@@rollback=yes" in result.stdout
    assert not conf.exists()
    assert sum(line.startswith("reload") for line in log.read_text().splitlines()) == 2


def test_existing_file_and_wrong_confirmation_are_preserved(server):
    run, conf, _, _ = server
    assert run("apply", TOKEN).returncode == 0
    original = conf.read_text()
    assert run("apply", "b" * 32).returncode != 0
    assert run("confirm", "b" * 32).returncode != 0
    assert conf.read_text() == original


def test_authorized_key_is_checked_and_added_separately_without_duplicates(server):
    run, conf, home, log = server
    assert "@@authorized=no" in run("status", "ssh-ed25519", "AAAA").stdout
    for _ in range(2):
        result = run("add-key", "ssh-ed25519", "AAAA")
        assert result.returncode == 0, result.stderr
    keys = home / ".ssh/authorized_keys"
    assert keys.read_text().count("ssh-ed25519 AAAA") == 1
    assert keys.stat().st_mode & 0o777 == 0o600
    assert keys.parent.stat().st_mode & 0o777 == 0o700
    assert "@@authorized=yes" in run("status", "ssh-ed25519", "AAAA").stdout
    assert not conf.exists() and not log.exists()


def test_key_does_not_follow_symlink(server, tmp_path):
    run, _, home, _ = server
    target = tmp_path / "untouched"
    target.mkdir()
    (home / ".ssh").symlink_to(target, target_is_directory=True)
    assert run("add-key", "ssh-ed25519", "AAAA").returncode != 0
    assert list(target.iterdir()) == []


def test_root_key_follows_cluster_symlink_without_chmod(server, tmp_path):
    run, conf, home, log = server
    (home / ".ssh").mkdir()
    target = tmp_path / "pve-priv-authorized_keys"
    original = "ssh-rsa AAAAcluster admin\n"
    target.write_text(original)
    target.chmod(0o640)
    keys = home / ".ssh/authorized_keys"
    keys.symlink_to(target)
    for command in ("touch", "chmod"):
        stubs(tmp_path, {command: f'''for file do
    [ "$file" != .ssh/authorized_keys ] || exit 1
done
exec /bin/{command} "$@"
'''})
    assert "@@authorized=no" in run("status", "ssh-ed25519", "AAAA", user="root").stdout
    for _ in range(2):
        result = run("add-key", "ssh-ed25519", "AAAA", user="root")
        assert result.returncode == 0, result.stderr
        assert "@@authorized=yes" in result.stdout
    assert keys.is_symlink() and keys.readlink() == target
    assert target.read_text() == original + "\nssh-ed25519 AAAA\n"
    assert target.stat().st_mode & 0o777 == 0o640
    assert "@@authorized=yes" in run("status", "ssh-ed25519", "AAAA", user="root").stdout
    assert not conf.exists() and not log.exists()


def test_key_file_symlink_is_rejected_for_other_users(server, tmp_path):
    run, _, home, _ = server
    (home / ".ssh").mkdir()
    target = tmp_path / "untouched"
    target.write_text("ssh-rsa AAAAold admin\n")
    keys = home / ".ssh/authorized_keys"
    keys.symlink_to(target)
    assert run("add-key", "ssh-ed25519", "AAAA").returncode != 0
    assert keys.is_symlink()
    assert target.read_text() == "ssh-rsa AAAAold admin\n"


@pytest.mark.parametrize("op,args", [("apply", []), ("apply", ["../bad"]), ("off", ["extra"]),
                                     ("anything", []), ("add-key", ["ssh-ed25519", "$(id)"]),
                                     ("status", ["ssh-ed25519"]), ("add-key", ["bad", "AAAA"])])
def test_bad_arguments_do_not_change_ssh(server, op, args):
    run, conf, home, _ = server
    assert run(op, *args).returncode != 0
    assert not conf.exists() and not (home / ".ssh").exists()


def test_sudo_cannot_add_a_key_for_another_user(server):
    run, conf, home, _ = server
    assert run("add-key", "ssh-ed25519", "AAAA", user="root", sudo_user="deck").returncode != 0
    assert not conf.exists() and not (home / ".ssh").exists()


def test_existing_key_options_are_preserved(server):
    run, _, home, _ = server
    (home / ".ssh").mkdir()
    keys = home / ".ssh/authorized_keys"
    original = 'from="10.0.0.1",no-port-forwarding ssh-ed25519 AAAA deck\n'
    keys.write_text(original)
    assert "@@authorized=yes" in run("status", "ssh-ed25519", "AAAA").stdout
    assert run("add-key", "ssh-ed25519", "AAAA").returncode == 0
    assert keys.read_text() == original


def test_configuration_symlink_is_rejected(server, tmp_path):
    run, conf, _, _ = server
    conf.parent.mkdir(parents=True)
    target = tmp_path / "foreign"
    target.write_text("unverändert\n")
    conf.symlink_to(target)
    assert run("apply", TOKEN).returncode != 0
    assert run("off").returncode != 0
    assert target.read_text() == "unverändert\n"


def _root_keys(tmp_path, text):
    keys = tmp_path / "roothome/.ssh/authorized_keys"
    keys.parent.mkdir(parents=True)
    keys.write_text(text)


@pytest.mark.parametrize("keys,root", [
    ('from="10.0.0.1" ssh-ed25519 AAAAadmin nico@pc\n', "prohibit-password"),
    ("# ssh-ed25519 AAAAold\n\n", "no"),
    (None, "no"),
])
def test_root_with_keys_is_not_locked_out_by_other_user(server, tmp_path, keys, root):
    run, conf, home, _ = server
    (tmp_path / "roothome").mkdir()
    if keys is not None:
        _root_keys(tmp_path, keys)
    assert run("add-key", "ssh-ed25519", "AAAA").returncode == 0
    status = run("status")
    assert ("@@root_keys=yes" in status.stdout) == (root != "no")
    assert run("apply", TOKEN).returncode == 0
    assert f"PermitRootLogin {root}\n" in conf.read_text()
    assert run("confirm", TOKEN).returncode == 0
    assert "@@hardened=yes" in run("status").stdout
    assert (home / ".ssh/authorized_keys").read_text().count("ssh-ed25519 AAAA\n") == 1


def test_root_status_does_not_report_root_keys(server, tmp_path):
    run, _, _, _ = server
    _root_keys(tmp_path, "ssh-ed25519 AAAAadmin\n")
    assert "@@root_keys=yes" not in run("status", user="root").stdout


def _systemd_run(tmp_path, log, rc=0):
    stubs(tmp_path, {"systemd-run": f'echo "systemd-run $*" >> \'{log}\'\nexit {rc}\n'})


def test_watchdog_runs_as_its_own_systemd_unit(server, tmp_path):
    run, _, _, log = server
    _systemd_run(tmp_path, log)
    assert run("apply", TOKEN).returncode == 0
    lines = log.read_text().splitlines()
    call = next(line for line in lines if line.startswith("systemd-run"))
    helper = tmp_path / "shield-root"
    assert call == f"systemd-run --unit=nodvard-ssh-expire-{TOKEN} --collect --quiet /bin/sh {helper} ssh-hardening expire deck {TOKEN}"
    assert "watchdog" not in lines


EFFECTIVE_LINES = [
    ("passwordauthentication", "yes"),
    ("kbdinteractiveauthentication", "yes"),
    ("maxauthtries", "6"),
    ("permitrootlogin", "yes"),
]


def _report_other(tmp_path, name, value):
    (tmp_path / "report-filter").write_text(f"s/^{name} .*/{name} {value}/\n")


@pytest.mark.parametrize("name,value", EFFECTIVE_LINES)
@pytest.mark.parametrize("operation", ["apply", "confirm", "status"])
def test_each_effective_sshd_line_is_checked(server, tmp_path, name, value, operation):
    run, _, _, _ = server
    if operation != "apply":
        assert run("apply", TOKEN).returncode == 0
    if operation == "status":
        assert run("confirm", TOKEN).returncode == 0
    _report_other(tmp_path, name, value)
    result = run(operation, *([] if operation == "status" else [TOKEN]))
    assert "@@hardened=yes" not in result.stdout
    if operation == "status":
        assert "@@hardened=no" in result.stdout
    else:
        assert result.returncode != 0


def test_watchdog_falls_back_when_systemd_run_fails(server, tmp_path):
    run, _, _, log = server
    _systemd_run(tmp_path, log, rc=1)
    assert run("apply", TOKEN).returncode == 0
    wait_for(tmp_path / "watchdog")
    assert "watchdog" in log.read_text().splitlines()


def _reboot(tmp_path):
    shutil.rmtree(tmp_path / "run/nodvard-shield-ssh")


def test_unconfirmed_change_is_rolled_back_at_boot(server, tmp_path):
    run, conf, _, _ = server
    assert run("apply", TOKEN).returncode == 0
    unit = tmp_path / "etc/systemd/system/nodvard-ssh-revert.service"
    wants = tmp_path / "etc/systemd/system/multi-user.target.wants/nodvard-ssh-revert.service"
    assert "ssh-hardening boot-revert root" in unit.read_text() and "Before=ssh.service sshd.service" in unit.read_text()
    assert wants.is_symlink()
    _reboot(tmp_path)
    assert conf.exists()
    status = run("status").stdout
    assert "@@unconfirmed=yes" in status and "@@hardened=no" in status
    assert run("boot-revert", user="root").returncode == 0
    assert not conf.exists() and not unit.exists() and not wants.is_symlink()
    assert not (tmp_path / "etc/ssh/nodvard-haertung.pending").exists()
    assert "@@unconfirmed=yes" not in run("status").stdout


def test_boot_revert_leaves_a_change_inside_the_confirmation_window(server):
    run, conf, _, _ = server
    assert run("apply", TOKEN).returncode == 0
    assert run("boot-revert", user="root").returncode == 0
    assert conf.exists()


def test_confirmed_change_leaves_no_boot_marker(server, tmp_path):
    run, conf, _, _ = server
    assert run("apply", TOKEN).returncode == 0
    assert run("confirm", TOKEN).returncode == 0
    assert not (tmp_path / "etc/systemd/system/nodvard-ssh-revert.service").exists()
    _reboot(tmp_path)
    assert run("boot-revert", user="root").returncode == 0
    assert conf.exists()
    assert "@@hardened=yes" in run("status").stdout


def test_two_factor_pam_is_reported_and_blocks_apply(server, tmp_path):
    run, conf, _, _ = server
    assert "@@two_factor=yes" not in run("status").stdout
    pam = tmp_path / "etc/pam.d/sshd"
    pam.parent.mkdir(parents=True)
    pam.write_text("auth required pam_google_authenticator.so\n")
    assert "@@two_factor=yes" in run("status").stdout
    result = run("apply", TOKEN)
    assert result.returncode != 0 and "two-factor" in result.stderr
    assert not conf.exists()
    pam.write_text("# auth required pam_google_authenticator.so\n")
    assert "@@two_factor=yes" not in run("status").stdout


def test_authentication_methods_with_a_code_count_as_two_factor(server, tmp_path):
    run, _, _, _ = server
    cfg = tmp_path / "etc/ssh/sshd_config"
    cfg.parent.mkdir(parents=True)
    cfg.write_text("AuthenticationMethods publickey,keyboard-interactive\n")
    assert "@@two_factor=yes" in run("status").stdout
    cfg.write_text("AuthenticationMethods publickey\n")
    assert "@@two_factor=yes" not in run("status").stdout
