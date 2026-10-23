function extractPosts(targetId = '') {
  const selector = 'article[data-testid="tweet"]';
  return Array.from(document.querySelectorAll(selector)).slice(0, 1000).flatMap(article => {
    if (article.parentElement?.closest(selector)) return [];
    const owns = node => {
      if (node.closest(selector) !== article) return false;
      for (let parent = node.parentElement; parent && parent !== article; parent = parent.parentElement) {
        if (parent.matches('[role="link"],[data-testid="quoteTweet"],[data-testid="card.wrapper"]')) return false;
      }
      return true;
    };
    const own = query => Array.from(article.querySelectorAll(query)).filter(owns);
    const links = own('a[href]').flatMap(link => {
      if (link.closest('[data-testid="tweetText"]')) return [];
      try {
        const url = new URL(link.href);
        if (url.protocol !== 'https:' || !/^(www\.)?(x\.com|twitter\.com)$/.test(url.hostname)
            || url.username || url.password || url.port || !/^\/[A-Za-z0-9_]+\/status\/\d+$/.test(url.pathname)) return [];
        url.search = ''; url.hash = '';
        return [{link, url, id: url.pathname.split('/').pop()}];
      } catch { return []; }
    });
    const match = targetId ? links.find(item => item.id === targetId) :
      links.find(item => item.link.querySelector('time')) ||
      links.find(item => /^(www\.)?(x\.com|twitter\.com)$/.test(location.hostname) && item.url.pathname === location.pathname);
    if (!match) return [];
    const body = own('[data-testid="tweetText"]')[0];
    const text = body?.textContent || '';
    const state = (off, on) => {
      const a = own('[data-testid="' + off + '"]').length > 0, b = own('[data-testid="' + on + '"]').length > 0;
      return a === b ? null : b;
    };
    return [{id: match.id, url: match.url.href, text: text.slice(0, 200000),
      truncated: text.length > 200000 || own('[data-testid="tweet-text-show-more-link"]').length > 0,
      reactions: {like: state('like', 'unlike'), repost: state('retweet', 'unretweet'), bookmark: state('bookmark', 'removeBookmark')},
      relation: article.closest('[data-testid="placementTracking"]') ? 'recommendation' : article.getAttribute('data-reply-to') ? 'reply' : 'unknown',
      reply_to: article.getAttribute('data-reply-to') || null,
      author: (own('[data-testid="User-Name"]')[0]?.textContent || '').slice(0, 500),
      posted_at: match.link.querySelector('time')?.dateTime || '',
      social_context: (own('[data-testid="socialContext"]')[0]?.textContent || '').slice(0, 500),
      links: body ? Array.from(body.querySelectorAll('a[href]')).map(link => link.href).slice(0, 30) : [],
      images: own('[data-testid="tweetPhoto"] img').map(image => image.currentSrc || image.src).slice(0, 10),
      has_video: own('video,[data-testid="videoPlayer"]').length > 0}];
  });
}

const postsScript = targetId => `(${extractPosts.toString()})(${JSON.stringify(targetId || '')})`;
module.exports = {extractPosts, postsScript};
