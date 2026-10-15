(function (root) {
  function onboardingState(state, profiles, agent, selectedId, profilesState = 'ready',
    successfulProfiles = [], workspaceReady = false, historyState = 'ready') {
    const visible = state === 'unlocked' && profilesState === 'ready' ? profiles : [];
    const profile = visible.find(p => p.credential_id === selectedId)
      || visible.find(p => agent.running && p.config.name === agent.profile)
      || visible.find(p => p.status === 'verified') || visible[0];
    const opened = state === 'unlocked';
    const verified = opened && profile?.status === 'verified';
    const running = verified && agent.running && agent.profile === profile.config.name;
    const firstResult = verified && historyState === 'ready' && successfulProfiles.includes(profile.config.name);
    const action = !state ? 'loading' : !opened ? 'vault'
      : profilesState === 'loading' ? 'loading' : profilesState === 'failed' ? 'refresh' : !profile ? 'add'
      : !verified ? 'check' : !running ? 'start' : historyState !== 'ready' ? 'refresh'
      : firstResult ? 'chat' : !workspaceReady ? 'workspace' : 'first_task';
    return { profile, action, complete: [opened, opened && !!profile, !!verified, !!running, !!firstResult] };
  }
  if (typeof module !== 'undefined') module.exports = onboardingState;
  else root.igorekOnboardingState = onboardingState;
})(globalThis);
