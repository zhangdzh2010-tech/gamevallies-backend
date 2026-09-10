"""Tri-state compatibility and shared step requirements. Never infer verified support."""
from .generated_llm_routing_policy import LLM_ROUTING_POLICY

STEP_REQUIRED_CAPABILITIES = {key: tuple(caps) for key, caps in LLM_ROUTING_POLICY["stepCapabilities"].items()}

def capability_state(raw):
    flags = dict(raw) if isinstance(raw, dict) else {}
    sentinel = LLM_ROUTING_POLICY["legacyUncheckedFlags"]
    legacy_unchecked = set(flags) == set(sentinel) and all(flags[key] is value for key, value in sentinel.items())
    return ({"verified": False} if legacy_unchecked else flags), legacy_unchecked

def required_capabilities(step_key):
    key = step_key
    while key:
        if key in STEP_REQUIRED_CAPABILITIES:
            return STEP_REQUIRED_CAPABILITIES[key]
        key = key.rsplit(".", 1)[0] if "." in key else ""
    return ()

def capability_rejections(raw, step_key):
    flags, _ = capability_state(raw)
    unsafe = flags.get("unsafe_for_steps", [])
    unsafe = unsafe if isinstance(unsafe, list) else []
    rejected = [cap for cap in required_capabilities(step_key) if flags.get(cap) is False or cap in unsafe]
    if any(isinstance(key, str) and (step_key == key or step_key.startswith(key + ".")) for key in unsafe):
        rejected.append("explicit_step_restriction")
    return rejected
