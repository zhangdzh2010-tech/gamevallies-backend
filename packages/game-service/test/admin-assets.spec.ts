import { AdminController } from '../src/admin/admin.controller';

describe('admin release assets', () => {
  const html = '<link href="/admin/assets/admin-panel.css"><script src="/admin/assets/admin-panel-ops.js"></script>';
  let controller: AdminController;
  let version: string;
  const response = () => ({
    status: jest.fn().mockReturnThis(),
    end: jest.fn(), type: jest.fn(), setHeader: jest.fn(), send: jest.fn(),
  });

  beforeEach(() => {
    version = 'release-one';
    controller = new AdminController({} as any, {} as any);
    jest.spyOn(controller as any, 'readAdminAsset').mockImplementation((name: string) => ({
      body: name.endsWith('.html') ? html : 'asset',
      etag: name.endsWith('.html') ? '"unchanged-html"' : `"${version}"`,
    }));
  });

  it('versions script and stylesheet URLs so old browser caches cannot mix releases', () => {
    const res = response();
    controller.serveAdminPanel({ headers: {} } as any, res as any);
    expect(res.send).toHaveBeenCalledWith(
      '<link href="/admin/assets/admin-panel.css?v=release-one"><script src="/admin/assets/admin-panel-ops.js?v=release-one"></script>',
    );
  });

  it('invalidates HTML even when only a referenced asset changes', () => {
    const first = response();
    controller.serveAdminPanel({ headers: {} } as any, first as any);
    const previousEtag = first.setHeader.mock.calls.find(([name]) => name === 'ETag')![1];
    version = 'release-two';
    const next = response();
    controller.serveAdminPanel({ headers: { 'if-none-match': previousEtag } } as any, next as any);
    expect(next.status).not.toHaveBeenCalledWith(304);
    expect(next.send).toHaveBeenCalledWith(expect.stringContaining('?v=release-two'));
  });

  it('preserves revalidation headers on a not-modified asset response', () => {
    const res = response();
    controller.serveAdminAsset({ headers: { 'if-none-match': '"release-one"' } } as any, 'admin-panel-ops.js', res as any);
    expect(res.status).toHaveBeenCalledWith(304);
    expect(res.setHeader).toHaveBeenCalledWith('Cache-Control', 'no-cache');
    expect(res.setHeader).toHaveBeenCalledWith('ETag', '"release-one"');
    expect(res.send).not.toHaveBeenCalled();
  });
});
