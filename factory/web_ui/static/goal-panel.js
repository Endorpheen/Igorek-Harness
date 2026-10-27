(function (root, factory) {
  if (typeof module !== "undefined" && module.exports) module.exports = factory();
  else root.IgorekGoalPanel = factory();
})(typeof window === "undefined" ? globalThis : window, function () {
  "use strict";

  function mount(host, options) {
    let goal = null;
    let editing = false;
    let busy = false;
    const element = (tag, className, text) => {
      const node = document.createElement(tag);
      node.className = className;
      if (text !== undefined) node.textContent = text;
      return node;
    };
    const text = (ru, en) => options.language() === "en" ? en : ru;
    async function act(action, objective) {
      if (busy || !goal) return;
      busy = true;
      render();
      try {
        await options.action(action, {goal_id: goal.goal_id, ...(objective === undefined ? {} : {objective})});
        editing = false;
      } catch (error) {
        options.error(error);
      } finally {
        busy = false;
        render();
      }
    }
    function button(label, action) {
      const node = element("button", "goal-card-button", label);
      node.type = "button";
      node.disabled = busy;
      node.addEventListener("click", action);
      return node;
    }
    function render() {
      host.replaceChildren();
      host.hidden = !goal;
      if (!goal) return;
      const heading = element("div", "goal-card-heading");
      const title = element("strong", "goal-card-title", text("Цель", "Goal"));
      const labels = {
        active: text("Выполняется", "Active"), paused: text("Пауза", "Paused"),
        blocked: text("Остановлена", "Blocked"), complete: text("Выполнена", "Complete"),
        budget_limited: text("Лимит токенов", "Token limit"), usage_limited: text("Лимит использования", "Usage limit"),
      };
      heading.append(title, element("span", "goal-card-status", labels[goal.status] || goal.status));
      host.append(heading);
      if (editing) {
        const input = element("textarea", "goal-card-editor");
        input.value = goal.objective;
        input.maxLength = 4000;
        input.setAttribute("aria-label", text("Текст цели", "Goal text"));
        host.append(input);
        const actions = element("div", "goal-card-actions");
        actions.append(button(text("Сохранить", "Save"), () => {
          const objective = input.value.trim();
          if (objective) void act("edit", objective);
        }), button(text("Отмена", "Cancel"), () => { editing = false; render(); }));
        host.append(actions);
        return;
      }
      host.append(element("div", "goal-card-objective", goal.objective));
      const usage = String(goal.tokens_used || 0) + (goal.token_budget == null ? "" : " / " + goal.token_budget);
      host.append(element("div", "goal-card-usage", text("Токены: ", "Tokens: ") + usage));
      const actions = element("div", "goal-card-actions");
      if (goal.status === "active") actions.append(button(text("Пауза", "Pause"), () => void act("pause")));
      if (["paused", "blocked", "usage_limited"].includes(goal.status)) actions.append(button(text("Продолжить", "Resume"), () => void act("resume")));
      actions.append(button(text("Изменить", "Edit"), () => { editing = true; render(); }));
      actions.append(button(text("Убрать", "Clear"), () => void act("clear")));
      host.append(actions);
    }
    return {setGoal(value) { goal = value || null; editing = false; render(); }, render};
  }
  return {mount};
});
