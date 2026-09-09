"""Surviving a moment when CAMDS does not answer.

A six-hour import ended on one 30-second timeout with 1304 of 1536 Materials
already written. One slow moment is not a reason to stop - but which calls may
be sent again is not a detail. A timeout on an allocating call is ambiguous:
CAMDS may have created the node and lost the answer, so repeating it risks a
second Material or a duplicated substance. Those fail, and resume reconciles
against what CAMDS actually holds.

Not answering is not the only bad moment CAMDS has. It also answers HTTP 200
carrying "程序异常，请重试" - a fault at its end, with a request to try again.
That is separated here from a refusal that is a decision: the first is retried
on a read, the second stands wherever it arrives.
"""
import asyncio

import pytest

from camds_imds_importer.camds import api as api_module
from camds_imds_importer.camds.api import ATTEMPTS, CamdsApi, CamdsApiError, retryable


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch):
    """The backoff is real time; the behaviour under test is not."""
    async def instant(_seconds):
        return None
    monkeypatch.setattr(api_module.asyncio, "sleep", instant)


class Flaky:
    """Fails the first `failures` calls the way a dropped connection does."""

    def __init__(self, failures, status=200, body=None):
        self.failures = failures
        self.status = status
        self.body = body if body is not None else {"respCode": "0", "ok": True, "data": {"id": "x"}}
        self.attempts = 0

    async def _call(self, url, **kw):
        self.attempts += 1
        if self.attempts <= self.failures:
            raise asyncio.TimeoutError("Timeout 30000ms exceeded")
        outer = self

        class Response:
            status = outer.status

            @staticmethod
            async def json():
                return outer.body
        return Response

    post = _call
    get = _call


async def test_a_read_that_keeps_timing_out_still_fails_in_the_end():
    """Retrying is not pretending. After the attempts, the error is the real one."""
    flaky = Flaky(failures=ATTEMPTS)
    with pytest.raises(asyncio.TimeoutError):
        await CamdsApi(flaky, base_url="https://camds.test").load_tree("CA_5_1")
    assert flaky.attempts == ATTEMPTS


async def test_a_read_that_times_out_is_sent_again():
    flaky = Flaky(failures=2)
    api = CamdsApi(flaky, base_url="https://camds.test")
    assert await api.load_tree("CA_5_1") == {"id": "x"}
    assert flaky.attempts == 3
    assert api.retries == 2


async def test_an_allocating_call_is_never_sent_again():
    """CAMDS may have created the node and lost the answer."""
    flaky = Flaky(failures=1)
    api = CamdsApi(flaky, base_url="https://camds.test")
    with pytest.raises(asyncio.TimeoutError):
        await api.create_material_root("1.1.1")
    assert flaky.attempts == 1, "a second Material must never be allocated by a retry"


@pytest.mark.parametrize("path, may", [
    ("/api/mds/tree/loadNodeDate", True),
    ("/api/mds/tree/editNodeDate", True),      # posts a whole view; sending it twice lands the same
    ("/api/mds/tree/saveNodeDate", True),
    ("/api/mds/tree/addOrUpdateApply", True),  # an upsert
    ("/api/mds/tree/getMdsStatus/CA_5_1", True),
    ("/api/mds/material/createInitMaterial", False),
    ("/api/mds/component/createInitComponent", False),
    ("/api/mds/component/addComponentNodeToTree", False),
    ("/api/mds/semiComponent/addNewSemiComponentToTree", False),
    ("/api/mds/tree/addNewSubstanceToTree", False),
    ("/api/mds/tree/substituteMdsNode", False),
])
def test_only_calls_that_can_be_repeated_are_repeated(path, may):
    assert retryable(path) is may


async def test_a_gateway_hiccup_is_retried_but_a_refusal_is_not():
    busy = Flaky(failures=0, status=503)
    with pytest.raises(CamdsApiError, match="HTTP 503"):
        await CamdsApi(busy, base_url="https://camds.test").load_tree("CA_5_1")
    assert busy.attempts == ATTEMPTS, "tried, and then reported what CAMDS said"

    refused = Flaky(failures=0, status=403)
    with pytest.raises(CamdsApiError, match="HTTP 403"):
        await CamdsApi(refused, base_url="https://camds.test").load_tree("CA_5_1")
    assert refused.attempts == 1, "a refusal is an answer, not a hiccup"


async def test_a_defect_in_our_own_call_is_not_hidden_behind_retries():
    """A TypeError is a bug; three slow attempts would only obscure it."""
    class Wrong:
        async def post(self, url, params=None, data=None, headers=None, timeout=None):
            raise TypeError("unexpected keyword argument")

    with pytest.raises(TypeError):
        await CamdsApi(Wrong(), base_url="https://camds.test").load_tree("CA_5_1")


async def test_retries_are_reported_so_a_failing_session_is_visible():
    said = []
    flaky = Flaky(failures=1)
    api = CamdsApi(flaky, base_url="https://camds.test", on_retry=said.append)
    await api.load_tree("CA_5_1")
    assert said and "loadMdsTree" in said[0] and "retry 1" in said[0]


REFUSAL = "程序异常，请重试。如果重复出现请联系管理员处理！"


class Faulty:
    """Answers HTTP 200 carrying CAMDS's own "something went wrong, retry"."""

    def __init__(self, refusals):
        self.refusals = refusals
        self.attempts = 0

    async def _call(self, url, **kw):
        self.attempts += 1
        refused = self.attempts <= self.refusals
        body = ({"respCode": "1", "ok": False, "message": REFUSAL} if refused
                else {"respCode": "0", "ok": True, "data": {"id": "x"}})

        class Response:
            status = 200

            @staticmethod
            async def json():
                return body
        return Response

    post = _call
    get = _call


async def test_a_read_camds_says_to_retry_is_retried():
    """It arrives as an ordinary HTTP 200. Nothing marks it as transient except
    CAMDS's own sentence, which asks for exactly this. A resumed run had read
    188 Materials back and stopped on one of these."""
    faulty = Faulty(refusals=2)
    api = CamdsApi(faulty, base_url="https://camds.test")
    assert await api.load_tree("CA_5_1") == {"id": "x"}
    assert faulty.attempts == 3
    assert api.retries == 2


async def test_a_read_that_keeps_being_refused_reports_what_camds_said():
    faulty = Faulty(refusals=ATTEMPTS)
    with pytest.raises(CamdsApiError, match="程序异常"):
        await CamdsApi(faulty, base_url="https://camds.test").load_tree("CA_5_1")
    assert faulty.attempts == ATTEMPTS


async def test_a_write_camds_refuses_is_not_sent_again():
    """A refusal on a write is CAMDS considering the request and rejecting it.
    Sending it again either earns the same refusal or applies the change twice."""
    faulty = Faulty(refusals=1)
    api = CamdsApi(faulty, base_url="https://camds.test")
    with pytest.raises(CamdsApiError, match="程序异常"):
        await api.save("CA_21_1", "CA_5_1")
    assert faulty.attempts == 1
    assert api.retries == 0


async def test_a_refusal_that_is_not_a_camds_fault_stands():
    """Only CAMDS's own "try again" is treated as a bad moment. Anything else
    it says is an answer, and retrying would hide it behind three slow tries."""
    class Refuses:
        attempts = 0

        async def post(self, url, **kw):
            Refuses.attempts += 1

            class Response:
                status = 200

                @staticmethod
                async def json():
                    return {"respCode": "1", "ok": False, "message": "版本不存在"}
            return Response

    api = CamdsApi(Refuses(), base_url="https://camds.test")
    with pytest.raises(CamdsApiError, match="版本不存在"):
        await api.load_tree("CA_5_1")
    assert Refuses.attempts == 1
