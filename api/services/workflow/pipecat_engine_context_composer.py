"""System prompt and function schema composition for PipecatEngine nodes.

Extracts prompt and function composition logic from PipecatEngine into
reusable functions. Defines recording response mode markers and instructions.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Optional

if TYPE_CHECKING:
    from api.services.workflow.pipecat_engine_custom_tools import CustomToolManager
    from api.services.workflow.workflow_graph import Node, WorkflowGraph

from api.services.workflow.pipecat_engine_custom_tools import get_function_schema
from api.services.workflow.tools.knowledge_base import get_knowledge_base_tool

# ---------------------------------------------------------------------------
# Recording response mode markers
# ---------------------------------------------------------------------------

RECORDING_MARKER = "●"  # Play pre-recorded audio
TTS_MARKER = "▸"  # Generate dynamic TTS text

# ---------------------------------------------------------------------------
# Recording response mode system prompt instructions
# ---------------------------------------------------------------------------

RECORDING_RESPONSE_MODE_INSTRUCTIONS = """\
RESPONSE MODE INSTRUCTIONS - MANDATORY FORMAT:
Every response you generate MUST begin with excatcly one response mode indicator.
You have two modes for responding:

1. DYNAMIC SPEECH (▸): Generate text that will be converted to speech by TTS.
   Format: ▸ followed by a space and your full spoken response. Nothing else.
   Example: ▸ Hello! How can I help you today?

2. PRE-RECORDED AUDIO (●): Play a pre-recorded audio message.
   Format: ● followed by a space followed by recording_id followed by provided transcript. Nothing else.
   Example: ● rec_greeting_01 [ Provided Transcript ]

RULES:
- Your response MUST start with either ▸ or ● as the very first character.
- For ▸ (dynamic speech): Follow with a space and your response to be generated using TTS engine. Dont mix with ●
- For ● (pre-recorded audio): Follow with a space and recording_id of the audio clip with its transcript. Dont mix with ▸
- Use ● when a pre-recorded message matches the situation well.
- Use ▸ when you need to generate a dynamic, contextual response.
- *NEVER* mix modes in a single response, since we rely on the markers to decide whether to play using TTS or Pre-recorded audio."""


@dataclass(frozen=True)
class ComposedNodePrompt:
    text: str
    recording_enabled: bool


NATIVE_CALENDAR_PROMPT_DIRECTIVES = """\
### NATIVE CALENDAR & APPOINTMENT INSTRUCTIONS (STRICT ANTI-HALLUCINATION RULES):
- You have direct access to native platform calendar tools: 'check_available_slots', 'book_appointment', and 'lookup_appointment'.
- STRICT RULES AGAINST HALLUCINATION:
  1. NEVER claim, assume, or hallucinate that an appointment is booked or confirmed until you execute 'book_appointment' and receive success=true with an appointment_id.
  2. When a customer says they already booked, asks about their appointment, or provides their phone number/name: ALWAYS call 'lookup_appointment' first. DO NOT claim they have an appointment without looking it up!
  3. If 'lookup_appointment' returns no appointments (found=false), tell the customer politely that no appointment was found for their details, and offer to schedule a new one.
  4. If an appointment is found or booked, inform the customer with the actual booking ID, date, time, and status from the tool result.
  5. NEVER promise that a WhatsApp, SMS, or external message has been sent unless an actual messaging tool was executed. Always provide the booking ID directly in the conversation.
  6. To see available open times for any day, execute 'check_available_slots(date)'.
"""


def compose_system_prompt_for_node(
    *,
    node: "Node",
    workflow: "WorkflowGraph",
    format_prompt: Callable[[str], str],
    has_recordings: bool,
) -> ComposedNodePrompt:
    """Compose the full system prompt text for a workflow node.

    Combines the global prompt, node-specific prompt, and recording response
    mode instructions. Returns the same recording-mode decision for the router.

    Args:
        node: The workflow node to compose the prompt for.
        workflow: The full workflow graph (needed for global node prompt).
        format_prompt: Callable to render template variables in prompts.
        has_recordings: Whether the organization has active recordings.

    Returns:
        The composed text and whether this node uses recording response mode.
    """
    global_prompt = ""
    if workflow.global_node_id and node.add_global_prompt:
        global_node = workflow.nodes[workflow.global_node_id]
        global_prompt = format_prompt(global_node.prompt)

    formatted_node_prompt = format_prompt(node.prompt)

    parts = [p for p in (global_prompt, formatted_node_prompt) if p]

    recording_enabled = has_recordings and "RECORDING_ID:" in formatted_node_prompt
    if recording_enabled:
        parts.append(RECORDING_RESPONSE_MODE_INSTRUCTIONS)

    # Automatically append strict calendar directives if calendar booking is enabled or calendar intent is present
    prompt_lower = formatted_node_prompt.lower()
    has_calendar_intent = (
        getattr(node, "calendar_booking_enabled", False)
        or any(
            kw in prompt_lower
            for kw in [
                "appointment",
                "book_appointment",
                "schedule doctor",
                "receptionist",
                "clinic",
                "calendar booking",
                "check_available_slots",
                "lookup_appointment",
                "check appointment",
            ]
        )
    )
    if has_calendar_intent and not any(
        kw in formatted_node_prompt
        for kw in ["### APPOINTMENT", "### NATIVE CALENDAR", "STRICT ANTI-HALLUCINATION"]
    ):
        parts.append(NATIVE_CALENDAR_PROMPT_DIRECTIVES)

    return ComposedNodePrompt(
        text="\n\n".join(parts), recording_enabled=recording_enabled
    )


async def compose_functions_for_node(
    *,
    node: "Node",
    custom_tool_manager: Optional["CustomToolManager"],
    context_variables: Optional[dict] = None,
) -> list[dict]:
    """Compose the function/tool schemas for a workflow node.

    Gathers knowledge-base tools, custom tools (including built-in
    categories like calculator), and transition function schemas
    into a single list.

    Args:
        node: The workflow node to compose functions for.
        custom_tool_manager: Manager for custom and built-in tools (may be None).
        context_variables: Runtime context variables (including campaign settings).

    Returns:
        A list of function schemas to register with the LLM.
    """
    functions: list[dict] = []
    ctx = context_variables or {}

    # Knowledge base retrieval tool
    if node.document_uuids:
        kb_tool_def = get_knowledge_base_tool(node.document_uuids)
        kb_schema = get_function_schema(
            kb_tool_def["function"]["name"],
            kb_tool_def["function"]["description"],
            properties=kb_tool_def["function"]["parameters"].get("properties", {}),
            required=kb_tool_def["function"]["parameters"].get("required", []),
        )
        functions.append(kb_schema)

    # Custom tools
    if node.tool_uuids and custom_tool_manager:
        custom_tool_schemas = await custom_tool_manager.get_tool_schemas(
            node.tool_uuids,
            mcp_tool_filters=getattr(node, "mcp_tool_filters", None),
        )
        functions.extend(custom_tool_schemas)

    # Transition function schemas
    for outgoing_edge in node.out_edges:
        function_schema = get_function_schema(
            outgoing_edge.get_function_name(), outgoing_edge.condition
        )
        functions.append(function_schema)

    # Calendar booking tools (when enabled on node, in campaign context, or present in system prompt/keywords)
    node_prompt = getattr(node, "prompt", "") or ""
    prompt_lower = node_prompt.lower()
    has_calendar_keyword = any(
        kw in prompt_lower
        for kw in [
            "appointment",
            "book_appointment",
            "schedule doctor",
            "receptionist",
            "clinic",
            "calendar booking",
            "check_available_slots",
            "lookup_appointment",
            "check appointment",
        ]
    )
    if (
        getattr(node, "calendar_booking_enabled", False)
        or bool(ctx.get("calendar_booking_enabled", False))
        or "APPOINTMENT / CALENDAR SCHEDULING" in node_prompt
        or "NATIVE CALENDAR" in node_prompt
        or "book_appointment" in node_prompt
        or has_calendar_keyword
    ):
        from api.services.workflow.tools.calendar import get_calendar_tools
        for tool_def in get_calendar_tools():
            func = tool_def["function"]
            functions.append(
                get_function_schema(
                    func["name"],
                    func["description"],
                    properties=func["parameters"]["properties"],
                    required=func["parameters"].get("required", []),
                )
            )

    # Call forwarding tool (when enabled on node, in campaign context, or forwarding phone number is in prompt)
    if (
        getattr(node, "call_forwarding_enabled", False)
        or getattr(node, "forwarding_phone_number", None)
        or bool(ctx.get("call_forwarding_enabled", False))
        or bool(ctx.get("forwarding_phone_number"))
        or bool(ctx.get("transfer_destination"))
        or "CALL FORWARDING" in node_prompt
        or "forward the call to" in node_prompt
    ):
        functions.append(
            get_function_schema(
                "transfer_call",
                "Transfer or forward the live call to a human agent, manager, or specialist. Call this tool immediately when the customer requests to speak with a human or when transfer conditions are met.",
                properties={
                    "reason": {
                        "type": "string",
                        "description": "Optional reason for transferring the call.",
                    }
                },
                required=[],
            )
        )

    return functions
