"""离线规则演示：会话临时样例库，无A2A/MCP网络调用，无付费模型。"""
import tempfile
import uuid
from pathlib import Path
from TripWeave.services.rules import ProtocolDemoModel
from TripWeave.services.data import TravelStore, render_business
from TripWeave.services.rules import domain_plan
from .schemas import StepResult

EXAMPLES = ['北京2026-10-01的天气', '查同一天去上海的火车票', '订第二个', '1张']
DemoModel = ProtocolDemoModel


class DemoTransport:
    def __init__(self):
        self.temp = tempfile.TemporaryDirectory(prefix='tripweave-offline-')
        self.store = TravelStore(Path(self.temp.name) / 'demo.sqlite3')

    async def call(self, capability, step, dependency_results, *, operation='execute', arguments=None):
        if step.capability == 'order':
            if operation == 'prepare':
                data = self.store.prepare(**arguments)
            elif operation == 'commit':
                data = self.store.book(arguments['quote_id'], str(uuid.uuid4()))
            else:
                raise ValueError('必须先准备再确认')
        else:
            plan = domain_plan(step.capability, step.query, [])
            if plan['action'] != 'call':
                return StepResult(step_id=step.id, capability=step.capability, status='input_required', text=plan['message'])
            args = plan['arguments']
            if step.capability == 'tickets':
                data = self.store.tickets(**args)
            elif args['city'] in ('北京', '上海') and args['travel_date'] == '2026-10-01':
                data = {'status': 'success', 'city': args['city'], 'date': args['travel_date'], 'temperature_max': 24,
                        'temperature_min': 16, 'source': '离线虚构天气样例'}
            else:
                data = {'status': 'no_data', 'message': '样例仅含北京/上海2026-10-01的天气。'}
        return StepResult(step_id=step.id, capability=step.capability,
                          status='success' if data['status'] == 'success' else 'input_required',
                          text=render_business(step.capability, data), data=data,
                          trace=[{'protocol': 'offline', 'action': operation}])
