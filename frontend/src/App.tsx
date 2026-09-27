function App() {
  return (
    <main className="shell">
      <header className="topbar">
        <a className="brand" href="/" aria-label="StudyPilot 首页">
          <span className="brand-mark" aria-hidden="true">S</span>
          <span>StudyPilot</span>
        </a>
        <span className="environment">开发预览</span>
      </header>

      <section className="content" aria-labelledby="welcome-title">
        <div className="eyebrow">PERSONAL LEARNING SPACE</div>
        <h1 id="welcome-title">把学习变成可持续的进步</h1>
        <p className="intro">
          你的课程资料、练习和复习计划将在这里汇总。先从建立第一个学习空间开始。
        </p>

        <div className="workspace-row">
          <div className="workspace-icon" aria-hidden="true">学</div>
          <div className="workspace-copy">
            <strong>个人学习空间</strong>
            <span>课程资料、问答记录和学习进度集中管理</span>
          </div>
          <span className="status-dot" aria-label="服务正常" />
        </div>

        <div className="setup-note">
          <span className="note-indicator" aria-hidden="true" />
          <span>开发环境已就绪，账号注册将在下一阶段接入。</span>
        </div>
      </section>

      <footer className="footer">
        <span>StudyPilot</span>
        <span>学习空间准备中</span>
      </footer>
    </main>
  )
}

export default App
