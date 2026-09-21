"""Unit tests for the Vast provisioner."""
# pylint: disable=protected-access

from types import SimpleNamespace

import jsonschema
import pytest

from sky.provision.vast import instance
from sky.provision.vast import utils
from sky.utils import schemas


def _terms(instance_type: str,
           disk_size: int = 100,
           secure_only: bool = False) -> list[str]:
    return utils._build_offer_query(instance_type, disk_size,
                                    secure_only).split(' ')


def test_gpu_name_is_underscore_form_without_quotes() -> None:
    terms = _terms('1x-RTX_4090-32-65536')
    assert 'gpu_name=RTX_4090' in terms
    assert not any('"RTX 4090"' in term for term in terms)


def test_cpu_ram_is_an_integer_in_gb() -> None:
    terms = _terms('1x-RTX_4090-32-65536')
    assert 'cpu_ram>=64' in terms
    assert not any('.' in term for term in terms)


def test_disk_and_gpu_count_are_sent() -> None:
    terms = _terms('2x-A100_SXM4-64-262144', disk_size=512)
    assert 'gpu_name=A100_SXM4' in terms
    assert 'num_gpus=2' in terms
    assert 'disk_space>=512' in terms
    assert 'cpu_ram>=256' in terms


def test_no_geolocation_clause() -> None:
    # Snapshot regions must not exclude live offers elsewhere.
    assert 'geolocation' not in _terms('1x-RTX_4090-32-65536')


def test_dead_flags_are_not_sent() -> None:
    terms = _terms('1x-RTX_4090-32-65536')
    assert not any(term.startswith('chunked') for term in terms)
    assert not any(term.startswith('georegion') for term in terms)


def test_secure_only_adds_datacenter_and_hosting() -> None:
    terms = _terms('1x-RTX_4090-32-65536', secure_only=True)
    assert 'datacenter=true' in terms
    assert 'hosting_type>=1' in terms


def test_multi_word_gpu_name_keeps_its_underscores() -> None:
    terms = _terms('1x-RTX_6000_Ada-48-131072')
    assert 'gpu_name=RTX_6000_Ada' in terms


def test_register_ssh_key_attaches_to_the_instance(monkeypatch) -> None:
    """The key must be registered with Vast, not only appended by onstart_cmd.

    Vast owns the instance's authorized set and drops what it did not register,
    which strands a running cluster with ``Permission denied (publickey)``.
    """
    calls = []

    class _Client:

        def attach_ssh(self, instance_id: int, ssh_key: str):
            calls.append((instance_id, ssh_key))
            return {'success': True}

    monkeypatch.setattr(utils.vast, 'vast', _Client)
    utils._register_ssh_key('42', '  ssh-rsa AAAA  ')
    assert calls == [(42, 'ssh-rsa AAAA')]


def test_register_ssh_key_warns_instead_of_raising(monkeypatch, caplog) -> None:
    """A launch that works now must not abort, but the failure must be visible.

    Registering on the account already fails silently for team API keys;
    repeating that here would hide the same outage.
    """

    class _Client:

        def attach_ssh(self, instance_id: int, ssh_key: str):
            raise RuntimeError('boom')

    monkeypatch.setattr(utils.vast, 'vast', _Client)
    with caplog.at_level('WARNING'):
        utils._register_ssh_key('42', 'ssh-rsa AAAA')
    assert 'boom' in caplog.text


def _with_filters(monkeypatch, value):
    monkeypatch.setattr(utils.skypilot_config, 'get_nested',
                        lambda keys, default_value: value)


def test_operator_offer_filters_are_appended_verbatim(monkeypatch) -> None:
    _with_filters(monkeypatch, ['vms_enabled=true'])
    terms = _terms('1x-RTX_4090-32-65536', 50)
    assert terms[:4] == [
        'gpu_name=RTX_4090', 'num_gpus=1', 'disk_space>=50', 'cpu_ram>=64'
    ]
    assert terms[-1] == 'vms_enabled=true'


def test_offer_filters_config_schema() -> None:
    schema = schemas.get_config_schema()
    jsonschema.validate({'vast': {
        'offer_filters': ['vms_enabled=true']
    }}, schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({'vast': {
            'offer_filters': 'vms_enabled=true'
        }}, schema)


def test_no_filter_config_leaves_the_query_unchanged(monkeypatch) -> None:
    _with_filters(monkeypatch, [])
    assert _terms('1x-RTX_4090-32-65536', 50) == [
        'gpu_name=RTX_4090', 'num_gpus=1', 'disk_space>=50', 'cpu_ram>=64'
    ]


def test_launch_returns_its_own_head_on_a_shared_account(monkeypatch) -> None:
    other = {'name': 'other-head', 'status': 'RUNNING', 'ssh_port': 22}
    own = {'name': 'own-head', 'status': 'RUNNING', 'ssh_port': 22}
    reads = iter([{'other-id': other}, {'other-id': other, 'own-id': own}])
    monkeypatch.setattr(utils, 'list_instances', lambda: next(reads))
    monkeypatch.setattr(utils, 'launch', lambda **kwargs: 'own-id')
    config = SimpleNamespace(provider_config={},
                             authentication_config={},
                             docker_config={},
                             node_config={
                                 'ImageId': 'image',
                                 'InstanceType': 'type',
                                 'DiskSize': 60,
                                 'Preemptible': False
                             },
                             resume_stopped_nodes=True,
                             count=1,
                             ports_to_open_on_launch=[])
    record = instance.run_instances('region', 'own', 'own', config)
    assert record.head_instance_id == 'own-id'
