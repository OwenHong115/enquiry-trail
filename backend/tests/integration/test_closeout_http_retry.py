from datetime import datetime, timezone
from support.fake_clock import FakeClock
from support.fake_crm import FakeCrm
from test_r16_operator_retry import _drive_dead_sync, _claim, _handle, CSRF_HEADER


def test_retry_parameters_are_available_over_http(api_client, postgres_db):
    clock = FakeClock(datetime.now(timezone.utc))
    enquiry_id, _ = _drive_dead_sync(postgres_db, clock, "http-retry@example.com")
    detail = api_client.get(f"/api/v1/enquiries/{enquiry_id}")
    assert detail.status_code == 200
    jobs = detail.json()["related_jobs"]
    assert {job["kind"] for job in jobs} == {"prepare_enquiry", "sync_contact"}
    job = next(job for job in jobs if job["kind"] == "sync_contact")
    assert job["state"] == "dead"
    payload = {"expected_state": job["state"],
               "expected_updated_at": job["updated_at"],
               "reason": "Retry after correcting provider configuration"}
    route = f"/api/v1/jobs/{job['id']}/retry"
    response = api_client.post(route, json=payload,
        headers={CSRF_HEADER: api_client.csrf_token})
    assert response.status_code == 200
    assert response.json()["state"] == "pending"
    duplicate = api_client.post(route, json=payload,
        headers={CSRF_HEADER: api_client.csrf_token})
    assert duplicate.status_code == 409
    clock.advance_to(datetime.now(timezone.utc))
    claim = _claim(postgres_db, clock)
    assert claim is not None
    _handle(postgres_db, claim, clock, FakeCrm())
    recovered = api_client.get(f"/api/v1/enquiries/{enquiry_id}").json()
    assert recovered["processing_summary"] == "ready"
    assert next(j for j in recovered["related_jobs"]
                if j["kind"] == "sync_contact")["state"] == "succeeded"
