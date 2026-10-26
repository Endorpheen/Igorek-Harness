"""Verify delivery recovery after a damaged schedule result."""

from factory.core.scheduled_delivery import report_result, result_path
from test_scheduled_delivery import setup as setup, launch, finish


def test_corrupt_result_file_recovers_and_delivers_exactly_once(setup):
    """A corrupt schedule result file fails delivery; restoring it retries and delivers once."""
    service, scheduler, controller, source, root = setup
    identity, task_id = launch(setup)
    report_result(task_id, {'disposition': 'deliver', 'text': 'Финальный ответ рабочего запуска'})
    result_path(task_id).write_text('not-json{', encoding='utf-8')
    finish(task_id)
    controller.deliver_pending()
    assert service.get_session(source)['messages'] == []
    assert scheduler.get_schedule(identity).to_dict()['metadata']['delivery_status'] == 'delivery_failed'
    report_result(task_id, {'disposition': 'deliver', 'text': 'Финальный ответ рабочего запуска'})
    meta = scheduler.get_schedule(identity).to_dict()['metadata']
    meta['delivery_retry_at'] = 0
    scheduler.update_schedule(identity, metadata=meta)
    controller.deliver_pending()
    message, = service.get_session(source)['messages']
    assert message['text'] == 'Финальный ответ рабочего запуска'
    assert scheduler.get_schedule(identity).to_dict()['metadata']['delivery_status'] == 'delivered'
