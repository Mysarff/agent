import asyncio
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta

from .schemas import RunResult, StepResult, BookingQuote
from .session import SessionState, Turn, parse_turn, CONTEXT_PROMPT


@dataclass
class PendingAction:
    step: object
    quote: BookingQuote
    created_at: float
    trace: list


class Engine:
    """一个会话一个实例。聊天历史辅助理解，业务状态由结构化字段维护。"""
    def __init__(self, router, transport, model, timeout=35, concurrency=2):
        self.router, self.registry = router, router.registry
        self.transport, self.model = transport, model
        self.timeout, self.concurrency = timeout, concurrency
        self.pending = {}
        self.session = SessionState()

    def reset(self):
        self.pending.clear()
        self.session = SessionState()

    async def _execute(self, step, dependencies, operation='execute', arguments=None):
        capability = self.registry.items[step.capability]
        started = time.perf_counter()
        try:
            async def dispatch():
                if capability.handler == 'a2a':
                    return await self.transport.call(capability, step, dependencies, operation=operation, arguments=arguments)
                raw = await self.model.ask('suggestion',
                    '只生成一般旅行建议，不声称核实实时价格、政策或营业时间。输出JSON {text:string}。',
                    {'query': step.query, 'dependency_results': dependencies})
                if not isinstance(raw.get('text'), str) or not raw['text'].strip():
                    raise ValueError('建议为空')
                return StepResult(step_id=step.id, capability=step.capability, status='success', text=raw['text'])
            result = await asyncio.wait_for(dispatch(), self.timeout)
            if capability.data_notice:
                result.text = capability.data_notice + '\n\n' + result.text
        except TimeoutError:
            result = StepResult(step_id=step.id, capability=step.capability, status='failed',
                                text='步骤超时。若为确认提交，结果可能尚未返回，请核对模拟订单，勿重复发起新预订。', error_code='timeout')
        except Exception as exc:
            result = StepResult(step_id=step.id, capability=step.capability, status='failed',
                                text='步骤未完成，请检查参数或服务连接；未获得成功结果。', error_code=type(exc).__name__)
        result.elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        return result

    async def _prepare(self, step, dependencies):
        self.session.awaiting = ['order']
        arguments, missing = self.session.booking_arguments()
        if missing:
            return StepResult(step_id=step.id, capability='order', status='input_required', text=missing), None
        result = await self._execute(step, dependencies, 'prepare', arguments)
        if result.status != 'success':
            return result, None
        try:
            quote = BookingQuote.model_validate(result.data['quote'])
            if quote.ticket.id != arguments['ticket_id'] or quote.quantity != arguments['quantity']:
                raise ValueError('报价参数与用户选择不符')
            if abs(quote.amount - quote.ticket.price * quote.quantity) > 0.001 or quote.expires_at <= time.time():
                raise ValueError('报价金额或期限无效')
        except (KeyError, ValueError):
            result.status, result.text, result.error_code = 'failed', '报价未通过校验，没有生成确认按钮。', 'invalid_quote'
            return result, None
        token = str(uuid.uuid4())
        self.pending[token] = PendingAction(step.model_copy(deep=True), quote, time.monotonic(), list(result.trace))
        result.status = 'awaiting_confirmation'
        return result, token

    async def run(self, query, history=None):
        self.pending.clear()  # 每条新消息使此前确认按钮失效，修改数量必须重做报价。
        run = RunResult(run_id=str(uuid.uuid4()))
        if not query.strip() or len(query) > 4000:
            run.message = '请输入1至4000字的具体问题。'
            return run
        try:
            literal = parse_turn(query)
            raw = await self.model.ask('context', CONTEXT_PROMPT,
                                       {'query': query, 'current_date': datetime.now(timezone(timedelta(hours=8))).date().isoformat()})
            turn = Turn.model_validate(raw)
            # 金额/票号/数量/序号由明确输入落地，模型不能从聊天文本猜写操作参数。
            for key in ('ticket_id', 'candidate_index', 'quantity', 'travel_date'):
                if getattr(turn, key) is not None and getattr(literal, key) is None:
                    raise ValueError('请明确日期、票号、序号或数量')
                setattr(turn, key, getattr(literal, key))
            for key in ('city', 'departure_city', 'arrival_city'):
                value = getattr(turn, key)
                if value and value not in query:
                    raise ValueError('城市不是当前输入中的名称')
            if 'order' in turn.intents and 'order' not in literal.intents:
                raise ValueError('没有明确预订意图')
            turn.cancel = literal.cancel
            missing = self.session.update(turn, query)
            run.routing_trace['session'] = self.session.snapshot()
            if missing:
                run.message = missing
                return run
            requested = self.session.requested
            queries = {c: self.session.query_for(c, query) for c in requested}
            plan, trace = await self.router.route(query, history or [], {'requested': requested, 'queries': queries})
            run.routing_trace.update(trace)
            if plan.action == 'execute':
                capabilities = [s.capability for s in plan.steps]
                if set(capabilities) != set(requested) or len(capabilities) != len(set(capabilities)):
                    raise ValueError('计划必须与当前请求能力一致，每类一次')
                ticket_steps = [s.id for s in plan.steps if s.capability == 'tickets']
                for step in plan.steps:
                    step.query = queries[step.capability]
                    if step.capability == 'order' and ticket_steps and not set(ticket_steps) <= set(step.depends_on):
                        raise ValueError('预订必须依赖本轮查票')
            run.plan = plan
        except (ValueError, TypeError, KeyError):
            run.message = '没有执行工具：信息或计划未通过校验。请明确城市、日期(YYYY-MM-DD)、票号/序号和数量(1至5张)。'
            return run
        except Exception as exc:
            run.message = '理解或路由未完成，请检查模型/服务配置。未执行工具。'
            run.routing_trace['error_code'] = type(exc).__name__
            return run
        if run.plan.action != 'execute':
            run.message = run.plan.message
            return run
        completed = {}
        semaphore = asyncio.Semaphore(self.concurrency)
        async def read_step(step):
            async with semaphore:
                return await self._execute(step, [completed[d].model_dump() for d in step.depends_on])
        while len(completed) < len(run.plan.steps):
            ready = [s for s in run.plan.steps if s.id not in completed and all(d in completed for d in s.depends_on)]
            executable = []
            for step in ready:
                if any(completed[d].status != 'success' for d in step.depends_on):
                    completed[step.id] = StepResult(step_id=step.id, capability=step.capability, status='blocked', text='上游未成功，本步骤未执行。')
                elif step.capability == 'order':
                    completed[step.id], run.pending_token = await self._prepare(step, [completed[d].model_dump() for d in step.depends_on])
                else:
                    executable.append(step)
            if executable:
                results = await asyncio.gather(*(read_step(s) for s in executable))
                for result in results:
                    if result.capability == 'tickets':
                        try:
                            self.session.absorb_tickets(result)
                        except ValueError:
                            result.status, result.text = 'failed', '候选结果格式无效，请重新查询。'
                    completed[result.step_id] = result
        run.results = [completed[s.id] for s in run.plan.steps]
        run.routing_trace['session'] = self.session.snapshot()
        return run

    async def confirm(self, token):
        pending = self.pending.pop(token, None)
        if not pending or time.monotonic() - pending.created_at > 300 or pending.quote.expires_at <= time.time():
            return StepResult(step_id=pending.step.id if pending else 'confirmation', capability='order', status='blocked',
                              text='确认已失效、已处理或已过期，请重新发起请求。')
        # 不再调用LLM解释用户意图，唯一可提交的是用户看到的这份报价ID。
        result = await self._execute(pending.step, [], 'commit', {'quote_id': pending.quote.quote_id})
        result.trace = pending.trace + result.trace
        self.session.clear_booking()
        self.session.clear_candidates()
        return result

    def cancel(self, token):
        self.pending.pop(token, None)
        self.session.clear_booking()
