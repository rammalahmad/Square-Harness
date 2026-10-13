"""Shared physical request allocation, independent of cumulative cost limits.

Allocations never remove prompt material. A caller decides which exact source
units belong in a request and which bounded recovery is appropriate afterward.
"""
import copy
import json

from .agent import AgentError


CONTEXT_MARGIN = 512
POLICY_VERSION = 1


class CallContextError(AgentError):
    def __init__(self, message, details):
        super().__init__(message)
        self.details = details


def count_input(client, payload):
    """Count the complete backend request, or conservatively bound its input.

Backend counters receive messages, tools, structured output and template options
as one payload. Backends determine how their response schema affects input.
"""
    counter = getattr(client, 'count_input_tokens', None)
    count = counter(payload) if callable(counter) else None
    if count is None:
        material = {key: payload[key] for key in ('messages', 'tools', 'format') if key in payload}
        count = len(json.dumps(material, ensure_ascii=False).encode()) + 256
        return count, 'conservative UTF-8 byte estimate'
    if type(count) is not int or count < 0:
        raise AgentError('Invalid exact input token count')
    return count, 'server token count'


def allocate_call(client, payload, desired, minimum=128, explicit_cap=None):
    """Return ``(fitted_payload, allocation)`` with a physical output allowance.

``desired`` is a phase's requested allowance, not ordinary-chat ``--predict``.
``minimum`` expresses a role's useful/required output floor. A cumulative cost
policy can supply its remaining allowance as ``explicit_cap``; no such limit is
implied when absent. The input is never mutated or clipped.
"""
    if type(desired) is not int or desired <= 0 or type(minimum) is not int or not 0 < minimum <= desired:
        raise ValueError('Call output allowances must be positive integers with minimum <= desired')
    if explicit_cap is not None and (type(explicit_cap) is not int or explicit_cap < 0):
        raise ValueError('An explicit call output cap must be a nonnegative integer or null')
    fitted = copy.deepcopy(payload)
    options = fitted.get('options')
    context = options.get('num_ctx') if isinstance(options, dict) else None
    if type(context) is not int or context <= 0:
        raise ValueError('A model call needs a positive physical context limit')
    options['num_predict'] = desired
    # Tokenization discovers model capacity on some backends. The configured
    # request limit is not prompt material: leave it out only for this preflight
    # so a smaller discovered server limit can be reconciled before dispatch.
    preflight = copy.deepcopy(fitted)
    preflight['options'].pop('num_ctx', None)
    count, method = count_input(client, preflight)
    server_context = getattr(client, 'observed_context_tokens', None)
    if server_context is not None and (type(server_context) is not int or server_context <= 0):
        raise AgentError('Invalid observed model context capacity')
    configured_context = context
    if server_context is not None:
        context = min(context, server_context)
    options['num_ctx'] = context
    room = max(0, context - count - CONTEXT_MARGIN)
    cap = min(desired, room, explicit_cap if explicit_cap is not None else desired)
    allocation = {'policy_version': POLICY_VERSION, 'input_tokens': count, 'method': method,
                  'requested_output_tokens': desired, 'output_tokens': cap,
                  'minimum_output_tokens': minimum, 'context_tokens': context,
                  'configured_context_tokens': configured_context,
                  'server_context_tokens': server_context,
                  'context_source': 'configured' if server_context is None else 'server_and_configured',
                  'context_margin_tokens': CONTEXT_MARGIN,
                  'available_output_tokens': room, 'explicit_cap': explicit_cap}
    if cap < minimum:
        if explicit_cap is not None and explicit_cap < minimum:
            raise CallContextError(
                f'The explicit output allowance {explicit_cap} is below the required {minimum}. '
                'No hypotheses, sources or saved work were clipped.', allocation)
        raise CallContextError(
            f'The complete request needs {count} input tokens ({method}); context {context} '
            f'leaves {room} output tokens after framing, below the required {minimum}. '
            'No hypotheses, sources or saved work were clipped.', allocation)
    options['num_predict'] = cap
    return fitted, allocation
