import { useState } from "react";
import { NavLink } from "react-router-dom";

import type { Channel } from "../../api/types";
import { useAuth } from "../../auth/AuthContext";
import { Icon } from "../../components/Icon";
import { Avatar, shortWhen } from "../../components/ui";

export function ChannelIcon({ channel }: { channel: Channel }) {
  if (channel.kind === "direct") return <Avatar name={channel.title} />;
  const icon = channel.kind === "global" ? "globe" : channel.kind === "local" ? "pin" : "users";
  return (
    <span className={`channel-icon ${channel.kind}`} aria-hidden="true">
      <Icon name={icon} />
    </span>
  );
}

function preview(channel: Channel, myId: number): string {
  const m = channel.last_message;
  if (!m) return channel.subtitle;
  if (m.deleted) return "Message deleted";
  if (m.kind === "system") return m.body;
  const who = m.sender?.id === myId ? "You" : (m.sender?.display_name.split(" ")[0] ?? "");
  return channel.kind === "direct" && who !== "You" ? m.body : `${who}: ${m.body}`;
}

const SECTIONS: { label: string; kinds: Channel["kind"][] }[] = [
  { label: "Network", kinds: ["global", "local"] },
  { label: "Groups", kinds: ["group"] },
  { label: "Direct messages", kinds: ["direct"] },
];

export function ChannelList({
  channels,
  onNewMessage,
  onNewGroup,
}: {
  channels: Channel[];
  onNewMessage: () => void;
  onNewGroup: () => void;
}) {
  const { me } = useAuth();
  const [query, setQuery] = useState("");
  const q = query.trim().toLowerCase();
  const visible = q
    ? channels.filter((c) =>
        [c.title, c.subtitle, ...(c.members ?? []).map((m) => m.display_name)]
          .join(" ")
          .toLowerCase()
          .includes(q),
      )
    : channels;

  return (
    <aside className="chat-list" aria-label="Conversations">
      <div className="chat-list-head">
        <div className="search">
          <Icon name="search" size={16} />
          <input
            type="search"
            aria-label="Search conversations"
            placeholder="Search conversations"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
        <div className="actions">
          <button type="button" className="btn btn-secondary btn-sm" onClick={onNewMessage}>
            <Icon name="note" size={15} /> New message
          </button>
          <button type="button" className="btn btn-secondary btn-sm" onClick={onNewGroup}>
            <Icon name="users" size={15} /> New group
          </button>
        </div>
      </div>
      <div className="chat-sections">
        {SECTIONS.map((section) => {
          const items = visible.filter((c) => section.kinds.includes(c.kind));
          if (items.length === 0) return null;
          return (
            <div key={section.label}>
              <p className="chat-section-label">{section.label}</p>
              <ul className="channel-list">
                {items.map((c) => (
                  <li key={c.id}>
                    <NavLink
                      to={`/messages/${c.id}`}
                      className={({ isActive }) =>
                        ["channel", isActive && "active", c.unread_count > 0 && "unread"]
                          .filter(Boolean)
                          .join(" ")
                      }
                    >
                      <ChannelIcon channel={c} />
                      <span className="channel-text">
                        <span className="channel-top">
                          <span className="channel-title">{c.title}</span>
                          {c.last_message && (
                            <time className="channel-time" dateTime={c.last_message.created_at}>
                              {shortWhen(c.last_message.created_at)}
                            </time>
                          )}
                        </span>
                        <span className="channel-bottom">
                          <span className="channel-preview">{preview(c, me?.user.id ?? 0)}</span>
                          {c.unread_count > 0 && (
                            <span className="unread-pill">
                              {c.unread_count > 99 ? "99+" : c.unread_count}
                              <span className="sr-only"> unread</span>
                            </span>
                          )}
                        </span>
                      </span>
                    </NavLink>
                  </li>
                ))}
              </ul>
            </div>
          );
        })}
        {visible.length === 0 && (
          <p className="chat-none">
            {q ? "No conversations match your search." : "No conversations yet."}
          </p>
        )}
      </div>
    </aside>
  );
}
