import { useState } from "react";
import { useParams } from "react-router-dom";

import { useChannels } from "../../chat/queries";
import { Icon } from "../../components/Icon";
import { EmptyState, ErrorState, Loading, PageHeader } from "../../components/ui";
import { ChannelList } from "./ChannelList";
import { PeopleDialog } from "./PeopleDialog";
import type { PeopleMode } from "./PeopleDialog";
import { Thread } from "./Thread";

export function MessagesPage() {
  const { channelId } = useParams();
  const channels = useChannels();
  const [dialog, setDialog] = useState<PeopleMode | null>(null);
  const active = channels.data?.find((c) => c.id === Number(channelId));

  return (
    <section className={channelId ? "messages-page in-thread" : "messages-page"}>
      <PageHeader
        title="Messages"
        subtitle="Coordinate with every food bank in the network, your region, a group, or one person."
      />
      {channels.isPending ? (
        <Loading label="Loading conversations…" />
      ) : channels.isError ? (
        <ErrorState error={channels.error} onRetry={() => channels.refetch()} />
      ) : (
        <div className={channelId ? "chat has-thread" : "chat"}>
          <ChannelList
            channels={channels.data}
            onNewMessage={() => setDialog({ kind: "direct" })}
            onNewGroup={() => setDialog({ kind: "group" })}
          />
          {active ? (
            <Thread key={active.id} channel={active} onPeople={setDialog} />
          ) : (
            <div className="chat-thread thread-empty">
              <EmptyState
                icon={<Icon name="messages" size={24} />}
                title={channelId ? "Conversation not found" : "Pick a conversation"}
              >
                {channelId
                  ? "It may have been removed, or you are no longer a member."
                  : "Choose a channel on the left, or start a new message or group."}
              </EmptyState>
            </div>
          )}
        </div>
      )}
      {dialog && <PeopleDialog mode={dialog} onClose={() => setDialog(null)} />}
    </section>
  );
}
