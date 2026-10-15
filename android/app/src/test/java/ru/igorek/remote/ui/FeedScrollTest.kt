package ru.igorek.remote.ui

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class FeedScrollTest {
    @Test
    fun `bottom index counts pending approval cards that shift the feed`() {
        assertEquals(4, feedBottomIndex(pendingApprovals = 0, timelineSize = 5))
        assertEquals(6, feedBottomIndex(pendingApprovals = 2, timelineSize = 5))
    }

    @Test
    fun `empty timeline still has a placeholder item`() {
        assertEquals(1, feedItemCount(pendingApprovals = 0, timelineSize = 0))
        assertEquals(0, feedBottomIndex(pendingApprovals = 0, timelineSize = 0))
        assertEquals(1, feedBottomIndex(pendingApprovals = 1, timelineSize = 0))
    }

    @Test
    fun `negative sizes cannot shorten the feed`() {
        assertEquals(1, feedItemCount(pendingApprovals = -3, timelineSize = -1))
        assertEquals(0, feedBottomIndex(pendingApprovals = -3, timelineSize = -1))
    }

    @Test
    fun `visible tail keeps following`() {
        assertEquals(true, tailFollowState(lastVisibleIndex = 9, totalItems = 10))
        assertEquals(true, tailFollowState(lastVisibleIndex = 8, totalItems = 10))
        assertEquals(true, tailFollowState(lastVisibleIndex = 7, totalItems = 10))
    }

    @Test
    fun `a batch of new cards no longer breaks the follow decision`() {
        // The decision is made based on the layout **before** the package appears: there was a visible tail of 10 elements.
        assertEquals(true, tailFollowState(lastVisibleIndex = 9, totalItems = 10))
        // The scroll goal is now calculated based on the new strip: 10 elements plus a package of three.
        // The previous code compared the old visible index with the new last index,
        // so a package of three or more cards was suppressing auto-scrolling.
        assertEquals(12, feedBottomIndex(pendingApprovals = 0, timelineSize = 13))
        assertEquals(false, tailFollowState(lastVisibleIndex = 9, totalItems = 13))
    }

    @Test
    fun `feed scrolled up stops following the tail`() {
        assertEquals(false, tailFollowState(lastVisibleIndex = 6, totalItems = 10))
        assertEquals(false, tailFollowState(lastVisibleIndex = 0, totalItems = 10))
    }

    @Test
    fun `unmeasured list keeps the previous decision`() {
        assertNull(tailFollowState(lastVisibleIndex = -1, totalItems = 10))
        assertEquals(true, tailFollowState(lastVisibleIndex = -1, totalItems = 0))
    }

    @Test
    fun `tail slack is configurable`() {
        assertEquals(false, tailFollowState(lastVisibleIndex = 6, totalItems = 10, tailSlack = 2))
        assertEquals(true, tailFollowState(lastVisibleIndex = 6, totalItems = 10, tailSlack = 3))
    }

    @Test
    fun `growing last card jumps instantly while a new card animates`() {
        assertTrue(feedScrollNeedsInstantJump(lastVisibleIndex = 11, bottomIndex = 11))
        assertTrue(feedScrollNeedsInstantJump(lastVisibleIndex = 12, bottomIndex = 11))
        assertFalse(feedScrollNeedsInstantJump(lastVisibleIndex = 10, bottomIndex = 11))
        assertFalse(feedScrollNeedsInstantJump(lastVisibleIndex = -1, bottomIndex = 5))
    }
}
