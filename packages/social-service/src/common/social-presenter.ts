export function presentUser(user: any) {
  if (!user) {
    return null;
  }

  return {
    id: user.id,
    username: user.username || user.displayName || '',
    email: user.email || undefined,
    phone: user.phone || undefined,
    avatar: user.avatarUrl || '',
    avatarUrl: user.avatarUrl || '',
    bio: user.bio || '',
    displayName: user.displayName || user.username || '',
    createdAt: user.createdAt,
    followerCount: user.followerCount ?? 0,
    followingCount: user.followingCount ?? 0,
    gameCount: user.gameCount ?? 0,
    totalPlays: Number(user.totalPlays ?? 0),
  };
}

export function presentComment(comment: any) {
  return {
    id: comment.id,
    gameId: comment.gameId,
    content: comment.content,
    authorId: comment.userId || comment.authorId || comment.user?.id || null,
    author: presentUser(comment.user || comment.author),
    likes: Number(comment.likeCount || comment.likes || 0),
    parentId: comment.parentId || null,
    replyCount:
      comment.replyCount ??
      comment._count?.replies ??
      (Array.isArray(comment.replies) ? comment.replies.length : 0),
    createdAt: comment.createdAt,
    replies: Array.isArray(comment.replies)
      ? comment.replies.map((reply: any) => presentComment(reply))
      : undefined,
  };
}

function buildNotificationTitle(type: string) {
  switch (type) {
    case 'like':
      return '收到新的点赞';
    case 'comment':
      return '收到新的评论';
    case 'follow':
      return '收到新的关注';
    case 'fork':
      return '作品被 Fork';
    default:
      return '系统通知';
  }
}

export function presentNotification(notification: any) {
  return {
    id: notification.id,
    type: notification.type === 'system' ? 'system' : notification.type,
    title: buildNotificationTitle(notification.type),
    body: notification.content || '',
    isRead: Boolean(notification.isRead),
    data: {
      targetId: notification.targetId || null,
      userId: notification.actorId || notification.actor?.id || null,
      gameId: notification.metadata?.gameId || notification.targetId || null,
    },
    createdAt: notification.createdAt,
    actor: presentUser(notification.actor),
  };
}
