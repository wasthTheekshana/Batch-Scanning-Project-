"""The deployment files (central/deploy): what they promise is checked, since
a broken deployment package is only discovered on the day it is needed."""
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
DEPLOY = ROOT / "central" / "deploy"


def read(name):
    return (DEPLOY / name).read_text(encoding="utf-8")


def copied_by_dockerfile():
    """[(source, destination)] of every COPY in the Dockerfile."""
    out = []
    for line in read("Dockerfile").splitlines():
        parts = line.split()
        if parts and parts[0] == "COPY":
            *sources, dest = parts[1:]
            out += [(s, dest) for s in sources]
    return out


def test_every_file_the_dockerfile_copies_exists():
    copies = copied_by_dockerfile()
    assert copies, "no COPY lines found"
    for source, _dest in copies:
        assert (ROOT / source).exists(), source


def test_the_dockerfile_copies_no_part_of_the_desktop_application():
    sources = [s for s, _ in copied_by_dockerfile()]
    assert all(s.startswith(("central", "projects/")) for s in sources), sources
    assert set(sources) >= {"projects/profile_manager.py", "projects/project_schema.json", "projects/__init__.py"}


def _image_tree(tmp_path):
    """The file tree the Dockerfile would build under /app (COPY semantics: a
    directory source copies its CONTENTS into the destination; files go into a
    destination that ends with "/")."""
    for source, dest in copied_by_dockerfile():
        src = ROOT / source.rstrip("/")
        target = tmp_path / dest.rstrip("/")
        if src.is_dir():
            shutil.copytree(src, target, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns("__pycache__", "deploy"))
        else:
            if dest.endswith("/"):
                target.mkdir(parents=True, exist_ok=True)
                target = target / src.name
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, target)


def _start_from(tmp_path):
    code = (f"import sys; sys.path.insert(0, {str(tmp_path)!r}); "
            "import central.app as a; app = a.create_app_from_env(); print(app.title)")
    env = {"CENTRAL_DATABASE_URL": f"sqlite:///{tmp_path / 'x.db'}",
           "CENTRAL_SESSION_SECRET": "0123456789abcdef0123456789abcdef",
           "SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", "")}
    # -I: nothing from the repository (or the current folder) can leak in
    return subprocess.run([sys.executable, "-I", "-c", code], cwd=tmp_path, env=env,
                          capture_output=True, text=True, timeout=120)


def test_the_service_starts_with_only_the_files_the_dockerfile_copies(tmp_path):
    """Rebuild the image's file tree, then start the app from it in a clean
    interpreter: a missing import would fail here, not on the server."""
    _image_tree(tmp_path)
    out = _start_from(tmp_path)
    assert out.returncode == 0, out.stderr[-800:]
    assert "dokscanner-central" in out.stdout


def test_that_check_really_notices_a_missing_file(tmp_path):
    _image_tree(tmp_path)
    (tmp_path / "projects" / "profile_manager.py").unlink()
    out = _start_from(tmp_path)
    assert out.returncode != 0 and "profile_manager" in out.stderr


def test_the_container_runs_as_an_unprivileged_user_with_a_healthcheck():
    text = read("Dockerfile")
    assert re.search(r"^USER\s+(?!root)\S+", text, re.M)
    assert "HEALTHCHECK" in text and "/healthz" in text
    assert "--proxy-headers" in text and "CENTRAL_WORKERS" in text


def test_the_compose_file_exposes_only_the_proxy_and_demands_its_secrets():
    import yaml
    compose = yaml.safe_load(read("docker-compose.yml"))
    services = compose["services"]
    assert set(services) == {"db", "central", "proxy"}
    assert "ports" not in services["db"] and "ports" not in services["central"]
    assert services["proxy"]["ports"] == ["80:80", "443:443"]
    env = services["central"]["environment"]
    assert env["CENTRAL_COOKIE_SECURE"] == "1"
    assert "?set CENTRAL_SESSION_SECRET" in env["CENTRAL_SESSION_SECRET"]    # refuses to start without it
    assert "?set POSTGRES_PASSWORD" in services["db"]["environment"]["POSTGRES_PASSWORD"]
    assert services["central"]["depends_on"]["db"]["condition"] == "service_healthy"
    assert "dbdata" in compose["volumes"]                                     # the data outlives the container


def test_both_proxies_cap_the_upload_size_the_readme_requires():
    assert re.search(r"max_size\s+1MB", read("Caddyfile"))
    assert re.search(r"client_max_body_size\s+1m;", read("nginx.conf.example"))
    assert "Strict-Transport-Security" in read("Caddyfile") + read("nginx.conf.example")


def test_the_env_example_names_every_variable_the_compose_file_needs():
    example = read(".env.example")
    needed = set(re.findall(r"\$\{([A-Z_]+)[:?-]", read("docker-compose.yml")))
    assert {"CENTRAL_DOMAIN", "POSTGRES_PASSWORD", "CENTRAL_SESSION_SECRET"} <= needed
    for name in needed:
        assert re.search(rf"^{name}=", example, re.M), name
    assert re.search(r"^POSTGRES_PASSWORD=$", example, re.M)              # no password shipped
    assert re.search(r"^CENTRAL_SESSION_SECRET=$", example, re.M)


def test_the_systemd_unit_is_locked_down_and_points_at_the_factory():
    unit = read("dokscanner-central.service")
    assert "central.app:create_app_from_env --factory" in unit
    assert "NoNewPrivileges=true" in unit and "ProtectSystem=strict" in unit
    assert "User=dokcentral" in unit and "CENTRAL_COOKIE_SECURE=1" in unit


def test_the_backup_script_is_valid_shell_and_checks_its_dump():
    sh = shutil.which("sh")
    if sh is None:
        pytest.skip("no sh")
    out = subprocess.run([sh, "-n", str(DEPLOY / "backup.sh")], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    text = read("backup.sh")
    assert "gzip -t" in text and "set -eu" in text and "KEEP" in text and "pg_dump" in text
    # confidential: created readable by the owner only
    assert "umask 077" in text and 'chmod 700 "$DEST"' in text and 'chmod 600 "$TMP"' in text
    assert text.index("umask 077") < text.index("mkdir -p")


def test_the_backup_script_keeps_the_newest_and_refuses_an_empty_dump(tmp_path):
    """Run it with a stand-in `docker` that prints a dump (or nothing)."""
    sh = shutil.which("sh")
    if sh is None or shutil.which("gzip") is None:
        pytest.skip("no sh/gzip")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "docker"
    fake.write_text('#!/bin/sh\nif [ -n "$FAKE_EMPTY" ]; then exit 0; fi\n'
                    'i=0; while [ $i -lt 600 ]; do echo "INSERT INTO t VALUES ($i, \'row-$((i*i*7919 % 100003))\');"; i=$((i+1)); done\n',
                    encoding="utf-8")
    fake.chmod(0o755)
    work = tmp_path / "w"
    work.mkdir()
    shutil.copy2(DEPLOY / "backup.sh", work / "backup.sh")
    dest = tmp_path / "out"
    import os
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
           "BACKUP_DIR": str(dest), "KEEP": "2"}
    for stamp in ("2020-01-01_000000", "2020-01-02_000000", "2020-01-03_000000"):
        dest.mkdir(exist_ok=True)
        (dest / f"dokcentral-{stamp}.sql.gz").write_bytes(b"old")
        os.utime(dest / f"dokcentral-{stamp}.sql.gz", (1_577_836_800 + int(stamp[8:10]), 1_577_836_800 + int(stamp[8:10])))
    ok = subprocess.run([sh, "backup.sh"], cwd=work, env=env, capture_output=True, text=True)
    assert ok.returncode == 0, ok.stderr
    files = sorted(p.name for p in dest.glob("dokcentral-*.sql.gz"))
    assert len(files) == 2 and "dokcentral-2020-01-01_000000.sql.gz" not in files   # oldest pruned
    assert not list(dest.glob("*.part"))
    if os.name != "nt":                      # Windows has no Unix permission bits to read back
        assert oct(dest.stat().st_mode & 0o777) == "0o700"
        newest = max(dest.glob("dokcentral-*.sql.gz"), key=lambda p: p.stat().st_mtime)
        assert oct(newest.stat().st_mode & 0o777) == "0o600"
    bad = subprocess.run([sh, "backup.sh"], cwd=work, env={**env, "FAKE_EMPTY": "1"},
                         capture_output=True, text=True)
    assert bad.returncode == 1 and "FAILED" in bad.stderr
    assert not list(dest.glob("*.part"))
    assert len(list(dest.glob("dokcentral-*.sql.gz"))) == 2                        # nothing deleted by a failed run


def test_the_build_context_is_limited_to_what_the_dockerfile_copies():
    """The context is the repository root (gigabytes of environment and
    installers); the ignore file must allow only the files that are copied."""
    lines = [l.strip() for l in read("Dockerfile.dockerignore").splitlines()
             if l.strip() and not l.strip().startswith("#")]
    assert lines[0] == "*"                                   # everything excluded first
    allowed = {l[1:] for l in lines if l.startswith("!")}
    assert allowed == {"central", "projects/__init__.py", "projects/profile_manager.py",
                       "projects/project_schema.json"}
    for source, _dest in copied_by_dockerfile():
        top = source.rstrip("/")
        assert top in allowed or top.split("/")[0] in allowed, source   # nothing the image needs is excluded


def test_the_data_key_is_optional_in_compose_and_explained_where_it_is_set():
    import yaml
    env = yaml.safe_load(read("docker-compose.yml"))["services"]["central"]["environment"]
    assert env["CENTRAL_DATA_KEY"] == "${CENTRAL_DATA_KEY:-}"                # empty: those features stay off
    example, guide = read(".env.example"), read("DEPLOY.md")
    assert re.search(r"^CENTRAL_DATA_KEY=$", example, re.M)                  # no key shipped
    assert "generate-key" in example and "APART FROM THE DATABASE" in example
    assert "generate-key" in guide and "apart from the database backups" in guide and "lost" in guide
    assert "CENTRAL_DATA_KEY" in read("dokscanner-central.service")
