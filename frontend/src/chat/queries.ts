import { useQuery } from "@tanstack/react-query";

import { api } from "../api/client";
import type { Channel, Person } from "../api/types";
import { useAuth } from "../auth/AuthContext";

export const CHANNELS_KEY = ["chat", "channels"] as const;

export function messagesKey(channelId: number) {
  return ["chat", "messages", channelId] as const;
}

export function canChat(role: string | undefined): boolean {
  return role === "admin" || role === "member";
}

export function useChannels() {
  const { me } = useAuth();
  return useQuery({
    queryKey: CHANNELS_KEY,
    queryFn: () => api<Channel[]>("/api/chat/channels"),
    enabled: canChat(me?.user.role),
  });
}

export function usePeople(enabled = true) {
  return useQuery({
    queryKey: ["chat", "people"],
    queryFn: () => api<Person[]>("/api/chat/people"),
    enabled,
    staleTime: 5 * 60_000,
  });
}

export function useUnreadTotal(): number {
  const channels = useChannels();
  return (channels.data ?? []).reduce((n, c) => n + c.unread_count, 0);
}
