import inspect
import sys
sys.path.insert(0, '.')

import core.persistence.decision_trace_writer as w

src = inspect.getsource(w)
print('MODULE LEN:', len(src))
print(src[:1500])
