# Agent Note: Working guidance in the prompt

Status: implemented — a built-in page on how to work, always first in the system prompt, with the user's own instructions after it

## Problem

Kaden's built-in system prompt was one sentence of persona ("You are Kaden, an AI agent.
Use the available tools to accomplish the user's goals.") and the facts of its tools. A
prompt the user wrote replaced it, tool facts included, so a user's Kaden ran on their
persona line alone. What the agent knew about how to work came from the tool descriptions,
the Memory module's rules and the model's own habits.

Guidance that belongs to no single tool had nowhere to live. Claude Code, OpenAI's Codex
CLI, Cline and Anthropic's published Claude prompts all carry guidance on how to work:
when to act and when to ask, checking work before calling it done, reporting plainly, care
with what cannot be undone, staying inside what was asked, how to write the answer.

## Decision

- **Three layers.** The base (how to work, in any job) is built in; a trade's guidance
  (coding, writing, research) belongs in a skill; the role (what this agent is for) is the
  user's instructions or a project's.
- **The base page** is `config.GUIDANCE`, in Kaden's own words, after the habits those
  prompts share: finish the task rather than describe it; understand before acting; make
  independent calls together; keep a plan for multi-step work; do what was asked and no
  more; after two failures look at why; weigh how hard an action is to undo and confirm
  what cannot be; treat what tools return as information, not instructions; never invent;
  check before saying done and say what was not checked; ask only when blocked or the
  choice is the user's, one question at a time; be direct, concise, prose first, honest;
  write down what will be needed later. It names no trade and no tool but TodoWrite.
- **The system prompt** is `RuntimeConfig.base_prompt` (`config.BASE_PROMPT`: the
  guidance, then `TOOL_FACTS`), then the user's own instructions
  (`RuntimeConfig.system_prompt`: Settings' Instructions, `[runtime] system_prompt` or
  `system_prompt_file`; "" = none), then the skills' listing and plan mode's section. The
  user's instructions are added after the guidance, never in its place.
- **Continuous** writes its own `base_prompt` (`life.PROMPT`, a life of its own rather than
  an assistant's framing) and drops the user's instructions, which are for their chats.
- About 550 words: every line is paid on every request, so it starts short and grows only
  from what real conversations show it lacks.

## Alternatives considered

- **Copy an industry prompt.** Claude Code's and the Claude.ai prompts are proprietary:
  their ideas may be used, not their text. Codex's is Apache-2.0 and could be copied with
  attribution, but it is about 3,500 words, tuned to GPT-5, written for its patch tool,
  sandbox and approval modes, and a coding prompt; Cline's and Goose's are too thin to be
  worth copying.
- **One long general prompt that also carries coding**, as Claude Code's. Right for a tool
  that only codes; for a general agent every rule is paid for on every request, and a
  prompt that covers every trade a little is good at none.
- **Keep putting rules in tool descriptions.** Works for a rule about one tool. Rules about
  no tool have no home, and the guidance is scattered where nobody reads it whole.
- **The user's prompt replacing the base, as before.** Lets a user rewrite everything, but
  a persona line then costs them every working habit and the tools' facts.

## Consequences

- Every chat and sub-session gets the same working habits, whatever the user writes; a
  user who wants a different base sets `[runtime] base_prompt` in config.toml.
- About 700 more tokens per request, cheap after a conversation's first request through
  the prompt cache, not free.
- Untested against real conversations: there is no set of repeatable tasks yet, so wording
  is judged by reading runs. Too many rules make an agent stiff; what earns a place is
  ongoing work.
- No coding skill yet, so a coding task gets the general habits only.
