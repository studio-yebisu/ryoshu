import httpx
import pytest

from receipt_ledger import pipeline


@pytest.mark.parametrize('kind',['local','cloud-alias','small-cloud-manifest','unknown-format'])
def test_cloud_or_unknown_model_is_rejected_using_metadata_before_sending_any_receipt(kind,monkeypatch):
    requests=[]
    real_client=httpx.Client
    def handler(request):
        requests.append(request)
        if request.url.path=='/api/tags':
            return httpx.Response(200,json={'models':[{'name':'qwen3:4b','size':200 if kind=='small-cloud-manifest' else 2497293931}]})
        assert request.url.path=='/api/show'
        return httpx.Response(200,json={'remote_host':'https://cloud.invalid' if kind=='cloud-alias' else '',
            'details':{'format':'cloud' if kind=='unknown-format' else 'gguf'},'model_info':{'general.architecture':'qwen3'}})
    monkeypatch.setattr(pipeline.httpx,'Client',lambda **kwargs:real_client(transport=httpx.MockTransport(handler),**kwargs))
    if kind=='local':assert pipeline.local_models()[0]['name']=='qwen3:4b'
    else:
        with pytest.raises(pipeline.ModelSafetyError):pipeline.local_models()
    assert len(requests)==2
    assert all(request.url.host=='127.0.0.1' for request in requests)
    assert requests[1].content==b'{"model":"qwen3:4b"}'
    assert not any(request.url.path=='/api/chat' for request in requests)
