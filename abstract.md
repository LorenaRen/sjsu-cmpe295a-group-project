# Abstract

**LLM-Driven Ethical Conflict Resolution for Multi-Robot Coordination**

Haoran Jiang, Mu Chen, Shervan Shahparnia, Yumeng Ren

_Current iteration: 2026-09-29_

Autonomous mobile robots are beginning to move into shared human environments such as warehouses and hospitals, where fleets of robots must operate safely around people and each other while meeting ethical expectations described in emerging standards such as BS 8611 and IEEE 7001. Large language models (LLMs) are increasingly explored as a way to give robots this kind of flexible real-time ethical decision-making instead of relying only on fixed rules, making the governance of AI-assisted robot decision-making an active area of robotics research.

Large language models can help plan a single autonomous robot, but coordinating several LLM-controlled robots remains an open challenge, made harder by ethical conflict resolution: each robot's plan may look reasonable on its own, yet the group's plans can still be unsafe or unfair, such as when robots compete for limited resources or operate near humans. This raises a central issue of trust, because no existing approach checks robots' plans against each other, only against each robot's own rules.

We propose extending this single-robot governance model to the fleet level. Prior work validated a single wheeled robot; we introduce a shared checking layer that evaluates multiple robots' proposed plans together against safety, fairness, and policy rules before execution. Conflicts will be sent back to be replanned instead of letting any robot decide unilaterally. We evaluate this with wheeled autonomous mobile robots (AMRs) performing distinct tasks such as delivery and service, starting with two robots and extending to a larger fleet as time allows. The approach will be implemented and evaluated in a multi-robot environment, measuring approval rate without replanning, safety, and fairness of conflict resolution across scenarios.
