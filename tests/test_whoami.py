"""Tests for the self-inspection helper.

Drives `whoami.inspect()` with synthetic env dicts: a real notebook pod's
identity vars (captured live 2026-06), a training-style env (no NB_PREFIX,
pytorch RANK/WORLD_SIZE, job uuid in hostname), and an outside-container env.
"""
from qzcli.core import whoami


NOTEBOOK_ENV = {
    "MY_POD_NAME": "e2emt-env-probe--5960071c5760-kxwfp4qadm",
    "HOSTNAME": "e2emt-env-probe--5960071c5760-kxwfp4qadm",
    "SERVER_TYPE": "NOTEBOOK",
    "NB_PREFIX": ("/ws-6e6ba362-e98e-45b2-9c5a-311998e93d65"
                  "/project-e362489a-3eb5-465f-9165-a494d41c55d4"
                  "/user-a41838c9-03c6-4dcf-a204-1989ea929627"
                  "/jupyter/44726a86-b793-4db2-aa01-5960071c5760"
                  "/5b102c9a-3e7a-4602-af71-d299e8be14bf"),
    "INSPIRE_GLOBAL_PUBLIC": "/inspire/hdd/global_public",
    "INSPIRE_GLOBAL_USER": "/inspire/hdd/global_user/mengzian-253108100064",
    "INSPIRE_PROJECT_USER_hdd": "/inspire/hdd/project/agileapplication/mengzian-253108100064",
    "INSPIRE_PROJECT_USER_ssd": "/inspire/ssd/project/agileapplication/mengzian-253108100064",
    "INSPIRE_PROJECT_PUBLIC_hdd": "/inspire/hdd/project/agileapplication/public",
    "WORKSPACE_DIR": "/inspire/hdd/project/agileapplication/mengzian-253108100064",
    "JUPYTER_SERVER_URL": "http://e2emt-env-probe--5960071c5760-kxwfp4qadm:8088/.../jupyter/44726a86-b793-4db2-aa01-5960071c5760/5b102c9a-3e7a-4602-af71-d299e8be14bf/",
}


def test_inside_notebook_pod_full_identity():
    r = whoami.inspect(NOTEBOOK_ENV)
    assert r["in_qz_container"] is True
    assert r["kind"] == "notebook"
    assert r["server_type"] == "NOTEBOOK"
    assert r["workspace_id"] == "ws-6e6ba362-e98e-45b2-9c5a-311998e93d65"
    assert r["project_id"] == "project-e362489a-3eb5-465f-9165-a494d41c55d4"
    assert r["user_id"] == "user-a41838c9-03c6-4dcf-a204-1989ea929627"
    assert r["notebook_id"] == "44726a86-b793-4db2-aa01-5960071c5760"
    assert r["instance_id"] == r["notebook_id"]
    assert r["jupyter_token"] == "5b102c9a-3e7a-4602-af71-d299e8be14bf"
    assert r["project_en_name"] == "agileapplication"
    assert "INSPIRE_PROJECT_USER_hdd" in r["gpfs"]
    assert r["jupyter_url"].startswith("http://")
    assert r["hostname"] == "e2emt-env-probe--5960071c5760-kxwfp4qadm"


def test_inside_training_pod_extracts_job_id_from_hostname():
    env = {
        "MY_POD_NAME": "job-3a86551f-d38c-4766-b7df-ffbe145ad8e4-worker-0",
        "INSPIRE_GLOBAL_PUBLIC": "/inspire/hdd/global_public",
        "INSPIRE_PROJECT_USER_hdd": "/inspire/hdd/project/agileapplication/mengzian-253108100064",
        "WORKSPACE_DIR": "/inspire/hdd/project/agileapplication/mengzian-253108100064",
        "RANK": "0", "WORLD_SIZE": "8", "LOCAL_RANK": "0",
        "MASTER_ADDR": "10.0.0.1", "MASTER_PORT": "29500",
    }
    r = whoami.inspect(env)
    assert r["in_qz_container"] is True
    assert r["kind"] == "training"
    assert r["job_id"] == "job-3a86551f-d38c-4766-b7df-ffbe145ad8e4"
    assert r["project_en_name"] == "agileapplication"
    assert r["dist"]["RANK"] == "0" and r["dist"]["WORLD_SIZE"] == "8"


def test_outside_container_reports_not_in_qz():
    r = whoami.inspect({"HOSTNAME": "my-laptop", "PATH": "/usr/bin"})
    assert r == {"in_qz_container": False, "hostname": "my-laptop"}


def test_unknown_kind_when_inspire_present_but_no_nb_prefix_no_rank():
    env = {
        "INSPIRE_GLOBAL_PUBLIC": "/inspire/hdd/global_public",
        "INSPIRE_PROJECT_USER_hdd": "/inspire/hdd/project/foo/u",
        "MY_POD_NAME": "weird-pod",
    }
    r = whoami.inspect(env)
    assert r["in_qz_container"] is True
    assert r["kind"] == "unknown"
    assert "job_id" not in r
    assert r["project_en_name"] == "foo"


def test_only_my_pod_name_set_is_enough_to_count_as_in_container():
    r = whoami.inspect({"MY_POD_NAME": "x"})
    assert r["in_qz_container"] is True
