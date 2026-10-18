/**
 * Pi-side ordering gate for the Application's model-authored spoken response.
 * The reserved confirm marker is an internal RPC handshake: the Application
 * returns true only after the robot has finished playback, without user UI.
 */
export default function registerVoiceReply(pi) {
  let round = { controller: new AbortController(), active: false, spoken: false };
  let reservation;

  pi.on('before_agent_start', () => {
    round.controller.abort();
    round = { controller: new AbortController(), active: true, spoken: false };
    reservation = undefined;
  });

  pi.on('tool_call', (event, ctx) => {
    if (!round.active || round.controller.signal.aborted || ctx.signal?.aborted) {
      return { block: true, reason: '本轮 speech 已取消，请等待新的用户请求。' };
    }
    if (event.toolName === 'voice_reply') {
      if (reservation) {
        return { block: true, reason: 'voice_reply 正在播报，请等待完成后再执行后续操作。' };
      }
      round.spoken = false;
      reservation = { id: event.toolCallId, round, signal: ctx.signal };
      return;
    }
    if (!round.spoken || round.authorizationSignal?.aborted) {
      return {
        block: true,
        reason: '请先调用 voice_reply，用一句贴合用户请求的自然回复说明将要做什么；等待播报完成后再调用操作工具。普通聊天直接回答即可。',
      };
    }
  });

  pi.registerTool({
    name: 'voice_reply',
    label: '先回应用户',
    description: '需要调用其他工具时，先用此工具向用户说一句自然、贴合请求的简短回应，例如“好的，我帮你打开小红书。”。等待本工具成功返回后再操作。普通聊天直接回答，无需调用。不得声称尚未执行的操作已完成。',
    promptSnippet: '在操作工具之前向用户播报自然的简短回应，并等待实际播放完成。',
    parameters: {
      type: 'object',
      properties: {
        text: { type: 'string', minLength: 1, maxLength: 80, description: '一句简短自然的中文回应，优先不超过28字，不重复用户原话，不声称任务已完成。' },
      },
      required: ['text'],
      additionalProperties: false,
    },
    executionMode: 'sequential',
    async execute(toolCallId, params, signal, _onUpdate, ctx) {
      const ticket = reservation;
      if (!ticket || ticket.id !== toolCallId || ticket.round !== round) {
        throw new Error('本轮 speech 已取消或缺少有效的播报请求。');
      }
      const combinedSignal = AbortSignal.any(
        [ticket.round.controller.signal, ticket.signal, signal, ctx.signal].filter(Boolean),
      );
      try {
        combinedSignal.throwIfAborted();
        const text = params?.text;
        if (typeof text !== 'string' || !text.trim() || [...text].length > 80 || Buffer.byteLength(text, 'utf8') > 900) {
          throw new Error('speech 回复必须是非空且不超过80字的文本。');
        }
        const complete = await ctx.ui.confirm('watcherobot.voice_reply.v1', text, {
          signal: combinedSignal,
          timeout: 45000,
        });
        combinedSignal.throwIfAborted();
        if (ticket.round !== round || complete !== true) {
          throw new Error('speech 播报未确认完成，后续操作仍被阻止。');
        }
        round.spoken = true;
        round.authorizationSignal = combinedSignal;
        return {
          content: [{ type: 'text', text: '先行回应已实际播报完成，现在可以执行本轮所需操作。' }],
          details: { spoken: true },
        };
      } finally {
        if (reservation === ticket) reservation = undefined;
      }
    },
  });
}
