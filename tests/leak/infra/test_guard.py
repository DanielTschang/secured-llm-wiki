"""The local-context guard, tested against throwaway kubeconfigs (no cluster needed)."""

import os
import subprocess
from pathlib import Path

import pytest

GUARD = Path(__file__).parents[3] / "scripts/require-local-context.sh"

pytestmark = pytest.mark.leak


def run_guard(*args: str, kubeconfig: Path | None = None) -> int:
    env = dict(os.environ)
    if kubeconfig is not None:
        env["KUBECONFIG"] = str(kubeconfig)
    return subprocess.run(
        [str(GUARD), *args], capture_output=True, text=True, env=env, check=False
    ).returncode


def kubeconfig(tmp_path: Path, ctx: str, server: str) -> Path:
    path = tmp_path / "config"
    path.write_text(
        f"""apiVersion: v1
kind: Config
clusters:
- name: c
  cluster: {{server: "{server}"}}
contexts:
- name: {ctx}
  context: {{cluster: c, user: u}}
users:
- name: u
  user: {{}}
current-context: {ctx}
"""
    )
    return path


@pytest.mark.parametrize(
    "ctx",
    ["", "danieltschang@mixing", "mixing", "kind", "prod-kind-kc", "kind-kc; rm -rf /", "k3d_x"],
)
def test_refuses_non_local_names(ctx: str) -> None:
    assert run_guard(ctx, "--allow-missing") != 0


def test_accepts_local_name_shape() -> None:  # kind only
    assert run_guard("kind-kc", "--allow-missing") == 0


@pytest.mark.parametrize(
    "args",
    [(), ("kind-kc", "--allow-missing", "x"), ("kind-kc", "--kube-context=prod")],
)
def test_refuses_bad_arguments(args: tuple[str, ...]) -> None:
    assert run_guard(*args) != 0


@pytest.mark.parametrize(
    "server",
    [
        "https://127.0.0.1:x@remote.example:6443",
        "https://127.0.0.1:6443@remote.example",
        "https://remote.example:6443",
        "https://127.0.0.1.remote.example:6443",
        "https://127.0.0.1:6443/../proxy",
        "http://127.0.0.1:6443",
    ],
)
def test_refuses_non_local_servers(tmp_path: Path, server: str) -> None:
    assert (
        run_guard("kind-zz-guardtest", kubeconfig=kubeconfig(tmp_path, "kind-zz-guardtest", server))
        != 0
    )


def test_refuses_local_looking_context_without_kind_cluster(tmp_path: Path) -> None:
    # e.g. an SSH tunnel on localhost named like a kind context.
    cfg = kubeconfig(tmp_path, "kind-zz-guardtest", "https://127.0.0.1:6443")
    assert run_guard("kind-zz-guardtest", kubeconfig=cfg) != 0


REPO = Path(__file__).parents[3]


@pytest.mark.parametrize(
    "cluster",
    [
        "kc'; touch {marker}; echo 'kc",
        "$(shell touch {marker})",
        "kc $(shell touch {marker})",
        "kc --kube-context=prod",
        "KC",
        "",
    ],
)
def test_makefile_rejects_bad_cluster_without_running_anything(
    tmp_path: Path, cluster: str
) -> None:
    marker = tmp_path / "INJECTED"
    result = subprocess.run(
        ["make", "-n", "-C", str(REPO), f"CLUSTER={cluster.format(marker=marker)}", "eval"],
        capture_output=True, text=True, check=False,
    )  # fmt: skip
    assert result.returncode != 0
    assert "CLUSTER must match" in result.stderr
    assert not marker.exists()


def test_makefile_accepts_plain_cluster() -> None:
    result = subprocess.run(
        ["make", "-n", "-C", str(REPO), "CLUSTER=kc-dev", "eval"],
        capture_output=True, text=True, check=False,
    )  # fmt: skip
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("server", ["https://localhost:6443", "https://[::1]:6443"])
def test_kind_requires_ipv4_loopback(tmp_path: Path, server: str) -> None:
    cfg = kubeconfig(tmp_path, "kind-zz-guardtest", server)
    assert run_guard("kind-zz-guardtest", kubeconfig=cfg) != 0


def test_k3d_refused_until_supported() -> None:
    assert run_guard("k3d-kc", "--allow-missing") != 0
