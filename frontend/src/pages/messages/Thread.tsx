import { useInfiniteQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Fragment, useEffect, useLayoutEffect, useRef, useState } from "react";
import type { KeyboardEvent } from "react";
import { Link, useNavigate } from "react-router-dom";

import { api, errorMessage, newIdempotencyKey } from "../../api/client";
import type { Channel, ChatMessage, MessagePage } from "../../api/types";
import { useAuth } from "../../auth/AuthContext";
import { CHANNELS_KEY, messagesKey } from "../../chat/queries";
import { Icon } from "../../components/Icon";
import { Alert, Avatar, ConfirmButton, dayLabel, formatTime } from "../../components/ui";
import { ChannelIcon } from "./ChannelList";
import type { PeopleMode } from "./PeopleDialog";

const PAGE = 50;
const MAX_LENGTH = 4000;
const GROUP_WINDOW_MS = 5 * 60_000;

interface Pending {
  client_id: string;
  body: string;
  created_at: string;
}

function subtitle(channel: Channel): string {
  if (channel.kind === "group") {
    return (channel.members ?? []).map((m) => m.display_name).join(", ");
  }
  if (channel.kind === "direct") return channel.subtitle;
  return `${channel.member_count} people · ${channel.subtitle}`;
}

function sameDay(a: string, b: string): boolean {
  return new Date(a).toDateString() === new Date(b).toDateString();
}

export function Thread({
  channel,
  onPeople,
}: {
  channel: Channel;
  onPeople: (mode: PeopleMode) => void;
}) {
  const { me } = useAuth();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const myId = me?.user.id ?? 0;
  const isAdmin = me?.user.role === "admin";
  const scroller = useRef<HTMLDivElement>(null);
  const composer = useRef<HTMLTextAreaElement>(null);
  const stickToBottom = useRef(true);
  const [draft, setDraft] = useState("");
  const [pending, setPending] = useState<Pending[]>([]);
  const [error, setError] = useState<string | null>(null);
  // Where "New messages" goes: the read position when the conversation was opened.
  const [readAtOpen] = useState(channel.last_read_message_id);

  const history = useInfiniteQuery({
    queryKey: messagesKey(channel.id),
    queryFn: ({ pageParam }) =>
      api<MessagePage>(
        `/api/chat/channels/${channel.id}/messages?limit=${PAGE}` +
          (pageParam ? `&before=${pageParam}` : ""),
      ),
    initialPageParam: 0,
    getNextPageParam: (last) => (last.has_more ? last.messages[0]?.id : undefined),
  });
  const messages: ChatMessage[] = (history.data?.pages ?? [])
    .slice()
    .reverse()
    .flatMap((p) => p.messages);
  const delivered = new Set(messages.map((m) => m.client_id).filter(Boolean));
  const waiting = pending.filter((p) => !delivered.has(p.client_id));
  const newest = messages.at(-1)?.id ?? 0;

  // Keep the newest message in view unless the reader scrolled up.
  useLayoutEffect(() => {
    const el = scroller.current;
    if (el && stickToBottom.current) el.scrollTop = el.scrollHeight;
  }, [newest, waiting.length]);

  // Loading older messages must not move what the reader is looking at.
  const oldest = messages[0]?.id;
  const heightBefore = useRef(0);
  useLayoutEffect(() => {
    const el = scroller.current;
    if (el && heightBefore.current) {
      el.scrollTop += el.scrollHeight - heightBefore.current;
      heightBefore.current = 0;
    }
  }, [oldest]);

  const markRead = useMutation({
    mutationFn: (messageId: number) =>
      api(`/api/chat/channels/${channel.id}/read`, {
        method: "POST",
        body: { message_id: messageId },
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: CHANNELS_KEY }),
  });
  const { mutate: sendRead } = markRead;
  useEffect(() => {
    if (newest > channel.last_read_message_id && document.visibilityState === "visible") {
      sendRead(newest);
    }
  }, [newest, channel.last_read_message_id, sendRead]);

  const send = useMutation({
    mutationFn: (p: Pending) =>
      api<ChatMessage>(`/api/chat/channels/${channel.id}/messages`, {
        method: "POST",
        body: { body: p.body, client_id: p.client_id },
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: messagesKey(channel.id) });
      void qc.invalidateQueries({ queryKey: CHANNELS_KEY });
    },
    onError: (err, p) => {
      setPending((cur) => cur.filter((x) => x.client_id !== p.client_id));
      setDraft((d) => d || p.body);
      setError(errorMessage(err));
    },
  });

  const remove = useMutation({
    mutationFn: (id: number) => api(`/api/chat/messages/${id}`, { method: "DELETE" }),
    onSettled: () => qc.invalidateQueries({ queryKey: messagesKey(channel.id) }),
    onError: (err) => setError(errorMessage(err)),
  });

  const leave = useMutation({
    mutationFn: () => api(`/api/chat/channels/${channel.id}/leave`, { method: "POST" }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: CHANNELS_KEY });
      navigate("/messages");
    },
    onError: (err) => setError(errorMessage(err)),
  });

  const submit = () => {
    const body = draft.trim();
    if (!body || body.length > MAX_LENGTH) return;
    const p = { client_id: newIdempotencyKey(), body, created_at: new Date().toISOString() };
    setError(null);
    setPending((cur) => [...cur, p]);
    setDraft("");
    stickToBottom.current = true;
    send.mutate(p);
    composer.current?.focus();
  };

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      submit();
    }
  };

  // Grow the composer with its content, up to the CSS max-height.
  useLayoutEffect(() => {
    const el = composer.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  }, [draft]);

  const firstUnread = messages.find(
    (m) => m.id > readAtOpen && m.sender?.id !== myId && readAtOpen > 0,
  )?.id;

  return (
    <section className="chat-thread" aria-labelledby="thread-title">
      <header className="thread-head">
        <Link to="/messages" className="icon-btn thread-back" aria-label="All conversations">
          <Icon name="back" />
        </Link>
        <ChannelIcon channel={channel} />
        <div className="thread-title">
          <h2 id="thread-title">{channel.title}</h2>
          <small>{subtitle(channel)}</small>
        </div>
        {channel.kind === "group" && (
          <div className="actions">
            <button
              type="button"
              className="icon-btn"
              aria-label="Add people"
              title="Add people"
              onClick={() =>
                onPeople({
                  kind: "add",
                  channelId: channel.id,
                  existing: (channel.members ?? []).map((m) => m.id),
                })
              }
            >
              <Icon name="userPlus" />
            </button>
            <ConfirmButton
              label="Leave"
              confirmLabel="Leave group"
              prompt="Leave this group?"
              className="btn btn-ghost btn-sm"
              busy={leave.isPending}
              onConfirm={() => leave.mutate()}
            />
          </div>
        )}
      </header>

      <div
        ref={scroller}
        className="messages"
        role="log"
        aria-label={`Messages in ${channel.title}`}
        aria-live="polite"
        tabIndex={0}
        onScroll={(e) => {
          const el = e.currentTarget;
          stickToBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
        }}
      >
        {history.hasNextPage && (
          <div className="load-older">
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              disabled={history.isFetchingNextPage}
              onClick={() => {
                heightBefore.current = scroller.current?.scrollHeight ?? 0;
                void history.fetchNextPage();
              }}
            >
              {history.isFetchingNextPage ? "Loading…" : "Load earlier messages"}
            </button>
          </div>
        )}
        {history.isPending && <p className="muted small">Loading messages…</p>}
        {history.isError && <Alert kind="error">{errorMessage(history.error)}</Alert>}
        {history.isSuccess && messages.length === 0 && waiting.length === 0 && (
          <p className="msg-system">
            <span>This is the start of your conversation in {channel.title}.</span>
          </p>
        )}

        {messages.map((m, i) => {
          const prev = messages[i - 1];
          const newDay = !prev || !sameDay(prev.created_at, m.created_at);
          const continued =
            !newDay &&
            prev?.kind === "user" &&
            m.kind === "user" &&
            prev.sender?.id === m.sender?.id &&
            new Date(m.created_at).getTime() - new Date(prev.created_at).getTime() <
              GROUP_WINDOW_MS &&
            m.id !== firstUnread;
          return (
            <Fragment key={m.id}>
              {newDay && <div className="day-divider">{dayLabel(m.created_at)}</div>}
              {m.id === firstUnread && <div className="unread-marker">New messages</div>}
              {m.kind === "system" ? (
                <p className="msg-system">
                  <span>{m.body}</span>
                </p>
              ) : (
                <article className={continued ? "msg continued" : "msg"}>
                  <div>{!continued && <Avatar name={m.sender?.display_name ?? "?"} />}</div>
                  <div className="msg-content">
                    {!continued && (
                      <div className="msg-meta">
                        <span className="msg-author">{m.sender?.display_name}</span>
                        <span className="msg-org">
                          {m.sender?.organization_name ?? "Network administrator"}
                        </span>
                        <time className="msg-time" dateTime={m.created_at}>
                          {formatTime(m.created_at)}
                        </time>
                      </div>
                    )}
                    <p className={m.deleted ? "msg-body deleted" : "msg-body"}>
                      {m.deleted ? "This message was deleted." : m.body}
                    </p>
                    {!m.deleted && (m.sender?.id === myId || isAdmin) && (
                      <div className="msg-tools">
                        <button
                          type="button"
                          className="icon-btn danger"
                          aria-label={
                            m.sender?.id === myId ? "Delete message" : "Remove message (moderation)"
                          }
                          title={m.sender?.id === myId ? "Delete message" : "Remove message"}
                          disabled={remove.isPending}
                          onClick={() => remove.mutate(m.id)}
                        >
                          <Icon name="trash" size={15} />
                        </button>
                      </div>
                    )}
                  </div>
                </article>
              )}
            </Fragment>
          );
        })}

        {waiting.map((p) => (
          <article key={p.client_id} className="msg continued">
            <div />
            <div className="msg-content">
              <p className="msg-body pending">{p.body}</p>
              <span className="sr-only">Sending…</span>
            </div>
          </article>
        ))}
      </div>

      <form
        className="composer"
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        {error && <Alert kind="error">{error}</Alert>}
        <div className="composer-box">
          <label htmlFor="composer" className="sr-only">
            Message {channel.title}
          </label>
          <textarea
            id="composer"
            ref={composer}
            rows={1}
            maxLength={MAX_LENGTH}
            placeholder={`Message ${channel.title}`}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={onKeyDown}
          />
          <button
            type="submit"
            className="btn btn-primary"
            aria-label="Send"
            disabled={!draft.trim()}
          >
            <Icon name="send" size={16} />
          </button>
        </div>
        <div className="composer-hint">
          <span>Enter to send · Shift + Enter for a new line</span>
          {draft.length > MAX_LENGTH - 500 && (
            <span>
              {draft.length} / {MAX_LENGTH}
            </span>
          )}
        </div>
      </form>
    </section>
  );
}
