package ru.igorek.remote.ui

/**
 * Clean geometry for session scrollable list (autoscroll).
 *
 * The list (`LazyColumn`) consists of three sections in this order:
 * 1) Cards awaiting approval (`pendingApprovalCards`);
 * 2) Timeline elements (`timeline`);
 * 3) A placeholder 'New Chat' if the timeline is empty.
 *
 * Two invariants cannot be expressed via `timeline.lastIndex`:
 * — Approval cards shift indices, so the bottom of the list is always greater than `timeline.lastIndex`;
 * — Even with an empty timeline, there is one element (placeholder), so the bottom of the list equals 0.
 */

/** How many actual elements are present in the `LazyColumn`. */
internal fun feedItemCount(pendingApprovals: Int, timelineSize: Int): Int {
    val approvals = if (pendingApprovals > 0) pendingApprovals else 0
    val timeline = if (timelineSize > 0) timelineSize else 1
    return approvals + timeline
}

/** The index of the last element in the feed is the goal for automatic scrolling 'down'. */
internal fun feedBottomIndex(pendingApprovals: Int, timelineSize: Int): Int =
    feedItemCount(pendingApprovals, timelineSize) - 1

/**
 * Determines whether the feed sticks to the bottom based on the actual list layout.
 *
 * Returns:
 * — `true`, if the feed's tail is visible (or the feed is empty);
 * — `false`, if the user scrolled up through history;
 * — `null`, if the layout hasn’t been determined yet (list not measured) — keep the previous decision,
 * otherwise, the first empty `layoutInfo` would reset the 'sticky' behavior, causing the feed to not reach the bottom
 * during opening of a long session.
 *
 * `[tailSlack]` — how many elements from the tail are considered 'almost the bottom': a new card that fits
 * into the screen next to the previous one should not disable scrolling.
 */
internal fun tailFollowState(
    lastVisibleIndex: Int,
    totalItems: Int,
    tailSlack: Int = TAIL_SLACK_ITEMS,
): Boolean? {
    if (totalItems <= 0) return true
    if (lastVisibleIndex < 0) return null
    return lastVisibleIndex >= totalItems - 1 - tailSlack
}

/**
 * Should the feed instantly scroll to the bottom without animation.
 *
 * If the bottom of the feed is already visible, it means only the height of the last card (text streaming,
 * expanding result of a tool) has increased. In this case, each time the next fragment disrupts the animation,
 * causing the feed to lag significantly — jump immediately. When a new element appears at the bottom,
 * there is a difference in indices, so we animate scrolling.
 */
internal fun feedScrollNeedsInstantJump(lastVisibleIndex: Int, bottomIndex: Int): Boolean =
    bottomIndex <= lastVisibleIndex

/** How many elements from the tail of the feed are considered the 'bottom'. */
internal const val TAIL_SLACK_ITEMS = 2
