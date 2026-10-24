from app.api.enquiries import map_detail_status


def test_dead_sync_keeps_intervention_when_provider_is_blocked():
    result = map_detail_status(sync_state="dead", provider_blocked=True,
        prepare_dead=False, requires_review=True, prepared_and_synced=False,
        sync_next_attempt_at=None)
    assert result.processing_summary == "failed"
    assert result.attention == "intervention_required"


def test_satisfied_enquiry_is_not_downgraded_by_shared_job():
    result = map_detail_status(sync_state="dead", provider_blocked=True,
        prepare_dead=False, requires_review=False, prepared_and_synced=True,
        sync_next_attempt_at=None)
    assert result.processing_summary == "ready"
    assert result.crm_status == "synced"
    assert result.attention == "none"
