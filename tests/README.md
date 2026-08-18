# 시험을 어떻게 돌리나

```
uv run pytest tests/ -q                 전부
uv run pytest tests/test_milestone.py   등식 하나: B == C == P
```

## 커버리지

```
uv run python -m coverage run -m pytest tests/ -q
uv run python -m coverage report
```

기준은 **100%** 다. 도달 불가한 자리는 `# pragma: no cover` 로 **이유와 함께**
표시돼 있으므로, 표시 없이 안 돌아가는 줄이 하나라도 생기면 실패한다.

표시된 것들은 두 종류다:

- 방어용 `AssertionError` -- 돌면 컴파일러 자신의 버그다
- 상위 검사가 먼저 걸러서 못 오는 갈래 -- 각 자리에 왜 못 오는지 적혀 있다

숫자를 올리려고 표시를 붙이지 말 것. 왜 못 오는지 한 줄로 쓸 수 없으면 그건
도달 가능한 줄이고, 시험이 필요한 것이다.
