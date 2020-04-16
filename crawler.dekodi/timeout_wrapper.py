from multiprocessing import Process, Queue
import time


def try_wrapper(*args, **kwargs):
    func = kwargs.pop('func_')
    q = kwargs.pop('queue_')
    ret = None
    try:
        ret = func(*args, **kwargs)
    except (Exception, BaseException) as e:
        print(e)
    q.put(ret)


def time_wrapper(func, args=None, kwargs=None, t=10):
    ret = None
    if not args:
        args = ()
    if not kwargs:
        kwargs = {}
    q = Queue()
    kwargs['queue_'] = q
    kwargs['func_'] = func
    p = Process(target=try_wrapper, args=args, kwargs=kwargs)
    p.start()
    t2 = time.time() + t
    while p.is_alive() and t2 > time.time() and q.empty():
        p.join(0.1)
    if not q.empty():
        ret = q.get()
    if p.is_alive():
        p.terminate()
    return ret
