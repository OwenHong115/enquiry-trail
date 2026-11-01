from app.api.enquiries import map_detail_status


def test_dead_sync_keeps_intervention_when_provider_is_blocked():
    result = map_detail_status(sync_state="dead", provider_blocked=True,
        prepare_dead=False, requires_review=True, prepared_and_synced=False,
        sync_next_attempt_at=None)
    assert result.processing_summary == "failed"
    assert result.attention == "needs_intervention"


def test_satisfied_enquiry_is_not_downgraded_by_shared_job():
    result = map_detail_status(sync_state="dead", provider_blocked=True,
        prepare_dead=False, requires_review=False, prepared_and_synced=True,
        sync_next_attempt_at=None)
    assert result.processing_summary == "ready"
    assert result.crm_status == "synced"
    assert result.attention == "none"


def test_prepare_dead_uses_needs_intervention_literal():
    result = map_detail_status(sync_state=None, provider_blocked=False,
        prepare_dead=True, requires_review=False, prepared_and_synced=False,
        sync_next_attempt_at=None)
    assert result.processing_summary == "failed"
    assert result.attention == "needs_intervention"
