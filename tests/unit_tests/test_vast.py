"""Unit tests for the Vast provisioner's offer-query builder."""

from sky.provision.vast import utils


def _terms(instance_type: str, disk_size: int = 100, secure_only: bool = False) -> list[str]:
    return utils._build_offer_query(instance_type, disk_size, secure_only).split(' ')


def test_gpu_name_is_underscore_form_without_quotes() -> None:
    terms = _terms('1x-RTX_4090-32-65536')
    assert 'gpu_name=RTX_4090' in terms
    # A quoted value with a space stops Vast's tokenizer and drops every later filter.
    assert not any('"RTX 4090"' in term for term in terms)


def test_cpu_ram_is_an_integer_in_gb() -> None:
    terms = _terms('1x-RTX_4090-32-65536')
    assert 'cpu_ram>=64' in terms
    # A decimal is truncated at the '.' by Vast's parser, dropping the rest of the query.
    assert not any('.' in term for term in terms)


def test_disk_and_gpu_count_are_sent() -> None:
    terms = _terms('2x-A100_SXM4-64-262144', disk_size=512)
    assert 'gpu_name=A100_SXM4' in terms
    assert 'num_gpus=2' in terms
    assert 'disk_space>=512' in terms
    assert 'cpu_ram>=256' in terms


def test_no_geolocation_clause() -> None:
    # The catalog region is a snapshot artifact; pinning on it rejects offers elsewhere.
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
