import {
  BadRequestException,
  ConflictException,
  HttpException,
  Injectable,
  Logger,
  ServiceUnavailableException,
  UnauthorizedException,
} from '@nestjs/common';
import { JwtService } from '@nestjs/jwt';
import { ConfigService } from '@nestjs/config';
import * as bcrypt from 'bcryptjs';
import { randomUUID } from 'crypto';
import Redis from 'ioredis';
import { AuthResponse, AuthRefreshResponse } from './dto';
import { PrismaService } from '../prisma/prisma.service';
import { SmsService } from './sms.service';

type VerificationPurpose = 'register' | 'login' | 'reset_password';
const VERIFICATION_TTL_SECONDS = 10 * 60;
const SEND_INTERVAL_SECONDS = parseInt(process.env.VERIFY_CODE_SEND_INTERVAL_SECONDS || '60', 10);

type WechatMiniappSession = {
  openid?: string;
  session_key?: string;
  unionid?: string;
  errcode?: number;
  errmsg?: string;
};

type WechatH5AccessTokenResponse = {
  access_token?: string;
  expires_in?: number;
  refresh_token?: string;
  openid?: string;
  scope?: string;
  unionid?: string;
  errcode?: number;
  errmsg?: string;
};

type WechatH5UserInfoResponse = {
  openid?: string;
  nickname?: string;
  headimgurl?: string;
  unionid?: string;
  errcode?: number;
  errmsg?: string;
};

type AuthWechatContext = {
  platform: 'miniapp' | 'h5';
  openId: string;
  appId: string;
};

type TokenUser = {
  id: string;
  username: string;
  email?: string;
  displayName: string;
  role: string;
};

const REFRESH_CONTEXT_PREFIX = 'auth:refresh-context:';
const WECHAT_H5_BINDING_PREFIX = 'auth:wechat-h5-openid:';

@Injectable()
export class AuthService {
  private readonly logger = new Logger(AuthService.name);
  private redis: Redis;

  constructor(
    private jwtService: JwtService,
    private configService: ConfigService,
    private prisma: PrismaService,
    private smsService: SmsService,
  ) {
    const redisUrl = process.env.REDIS_URL;
    if (redisUrl) {
      this.redis = new Redis(redisUrl);
    } else {
      this.redis = new Redis({ host: 'localhost', port: 6379 });
    }
  }

  private async fetchWechatMiniappSession(
    code: string,
  ): Promise<Required<Pick<WechatMiniappSession, 'openid' | 'session_key'>> & WechatMiniappSession> {
    const appId = this.configService.get<string>('wechat.miniappAppId');
    const appSecret = this.configService.get<string>('wechat.miniappAppSecret');

    if (!appId) {
      throw new BadRequestException('微信小程序 AppID 未配置');
    }

    if (!appSecret) {
      throw new BadRequestException('微信小程序 AppSecret 未配置');
    }

    const params = new URLSearchParams({
      appid: appId,
      secret: appSecret,
      js_code: code,
      grant_type: 'authorization_code',
    });

    const response = await fetch(`https://api.weixin.qq.com/sns/jscode2session?${params.toString()}`);
    if (!response.ok) {
      throw new BadRequestException(`微信登录请求失败: HTTP ${response.status}`);
    }

    const data = (await response.json()) as WechatMiniappSession;
    if (data.errcode || !data.openid || !data.session_key) {
      throw new BadRequestException(data.errmsg || '微信登录失败');
    }

    return data as Required<Pick<WechatMiniappSession, 'openid' | 'session_key'>> & WechatMiniappSession;
  }

  private async fetchWechatH5AccessToken(
    code: string,
  ): Promise<Required<Pick<WechatH5AccessTokenResponse, 'access_token' | 'openid'>> & WechatH5AccessTokenResponse> {
    const params = new URLSearchParams({
      appid: this.getWechatH5AppId(),
      secret: this.getWechatH5AppSecret(),
      code,
      grant_type: 'authorization_code',
    });

    const response = await fetch(`https://api.weixin.qq.com/sns/oauth2/access_token?${params.toString()}`);
    if (!response.ok) {
      throw new BadRequestException(`微信 H5 登录请求失败: HTTP ${response.status}`);
    }

    const data = (await response.json()) as WechatH5AccessTokenResponse;
    if (data.errcode || !data.access_token || !data.openid) {
      throw new BadRequestException(data.errmsg || '微信 H5 登录失败');
    }

    return data as Required<Pick<WechatH5AccessTokenResponse, 'access_token' | 'openid'>> & WechatH5AccessTokenResponse;
  }

  private async fetchWechatH5UserInfo(
    accessToken: string,
    openId: string,
  ): Promise<WechatH5UserInfoResponse> {
    const params = new URLSearchParams({
      access_token: accessToken,
      openid: openId,
      lang: 'zh_CN',
    });

    const response = await fetch(`https://api.weixin.qq.com/sns/userinfo?${params.toString()}`);
    if (!response.ok) {
      throw new BadRequestException(`微信 H5 用户信息请求失败: HTTP ${response.status}`);
    }

    const data = (await response.json()) as WechatH5UserInfoResponse;
    if (data.errcode) {
      throw new BadRequestException(data.errmsg || '微信 H5 用户信息获取失败');
    }

    return data;
  }

  private getWechatMiniappAppId(): string {
    const appId = this.configService.get<string>('wechat.miniappAppId');
    if (!appId) {
      throw new BadRequestException('微信小程序 AppID 未配置');
    }
    return appId;
  }

  private getWechatMiniappAppSecret(): string {
    const appSecret = this.configService.get<string>('wechat.miniappAppSecret');
    if (!appSecret) {
      throw new BadRequestException('微信小程序 AppSecret 未配置');
    }
    return appSecret;
  }

  private getWechatH5AppId(): string {
    const appId = this.configService.get<string>('wechat.h5AppId');
    if (!appId) {
      throw new BadRequestException('微信 H5 OAuth AppID 未配置');
    }
    return appId;
  }

  private getWechatH5AppSecret(): string {
    const appSecret = this.configService.get<string>('wechat.h5AppSecret');
    if (!appSecret) {
      throw new BadRequestException('微信 H5 OAuth AppSecret 未配置');
    }
    return appSecret;
  }

  private getWechatH5OauthScope(): string {
    return this.configService.get<string>('wechat.h5OauthScope') || 'snsapi_base';
  }

  buildWechatH5AuthorizeUrl(redirectUri: string, state: string, scope = this.getWechatH5OauthScope()) {
    if (!redirectUri) {
      throw new BadRequestException('微信 H5 授权缺少 redirectUri');
    }

    if (!state) {
      throw new BadRequestException('微信 H5 授权缺少 state');
    }

    this.assertSafeWechatH5RedirectUri(redirectUri);

    const appId = this.getWechatH5AppId();
    const params = new URLSearchParams({
      appid: appId,
      redirect_uri: redirectUri,
      response_type: 'code',
      scope,
      state,
    });

    return {
      appId,
      scope,
      authorizeUrl: `https://open.weixin.qq.com/connect/oauth2/authorize?${params.toString()}#wechat_redirect`,
    };
  }

  private assertSafeWechatH5RedirectUri(redirectUri: string) {
    const normalizedAllowedBase = (
      this.configService.get<string>('PUBLIC_WEB_BASE_URL')
      || this.configService.get<string>('PUBLIC_API_BASE_URL')
      || process.env.PUBLIC_WEB_BASE_URL
      || process.env.PUBLIC_API_BASE_URL
      || 'https://gamevallies.com'
    ).replace(/\/$/, '');

    try {
      const redirect = new URL(redirectUri);
      const allowed = new URL(normalizedAllowedBase);

      if (redirect.origin !== allowed.origin) {
        throw new BadRequestException('微信 H5 授权回调地址不在允许域名内');
      }
    } catch (error) {
      if (error instanceof BadRequestException) {
        throw error;
      }

      throw new BadRequestException('微信 H5 授权回调地址不合法');
    }
  }

  private async generateWechatUsername(openId: string): Promise<string> {
    const base = `wx_${openId.slice(-10).toLowerCase()}`;
    let username = base;
    let suffix = 0;

    while (await this.prisma.user.findFirst({ where: { username } })) {
      suffix += 1;
      username = `${base}${suffix}`;
    }

    return username;
  }

  async loginByPassword(account: string, password: string): Promise<AuthResponse> {
    const normalizedAccount = account.trim().toLowerCase();
    const user = await this.prisma.user.findFirst({
      where: {
        OR: [
          { phone: account },
          { username: normalizedAccount },
          { email: normalizedAccount },
        ],
      },
    });

    if (!user || !user.passwordHash) {
      throw new UnauthorizedException('账号或密码错误');
    }

    const valid = await bcrypt.compare(password, user.passwordHash);
    if (!valid) {
      throw new UnauthorizedException('账号或密码错误');
    }

    const tokens = await this.generateTokens({
      id: user.id,
      username: user.username,
      displayName: user.displayName ?? user.username,
      role: user.role,
    });

    return {
      ...tokens,
      user: {
        id: user.id,
        username: user.username,
        displayName: user.displayName ?? undefined,
        role: user.role,
      },
    };
  }

  async refreshToken(token: string): Promise<AuthRefreshResponse> {
    const refreshTokenRecord = await this.prisma.refreshToken.findUnique({
      where: { token },
      include: { user: true },
    });

    if (!refreshTokenRecord) {
      throw new UnauthorizedException('Refresh token not found');
    }

    if (refreshTokenRecord.revokedAt !== null) {
      throw new UnauthorizedException('Refresh token has been revoked');
    }

    if (refreshTokenRecord.expiresAt < new Date()) {
      throw new UnauthorizedException('Refresh token has expired');
    }

    const user = refreshTokenRecord.user;
    const authContext = await this.readRefreshContext(token);
    const tokens = await this.generateTokens({
      id: user.id,
      username: user.username,
      email: user.email ?? undefined,
      displayName: user.displayName ?? user.username,
      role: user.role,
    }, authContext);

    await this.revokeRefreshToken(token);

    return {
      accessToken: tokens.accessToken,
      refreshToken: tokens.refreshToken,
      expiresIn: tokens.expiresIn,
    };
  }

  async generateTokens(
    user: TokenUser,
    authContext?: AuthWechatContext | null,
  ): Promise<{ accessToken: string; refreshToken: string; expiresIn: number }> {
    const accessPayload: Record<string, unknown> = {
      sub: user.id,
      username: user.username,
      role: user.role,
    };

    if (authContext?.platform && authContext?.openId) {
      accessPayload.wechat = {
        platform: authContext.platform,
        openId: authContext.openId,
        appId: authContext.appId,
      };
    }

    const accessToken = this.jwtService.sign(
      accessPayload as any,
      {
        secret: this.configService.get<string>('JWT_SECRET'),
        expiresIn: this.configService.get<string>('JWT_EXPIRES_IN', '24h') as any,
      },
    );

    const expiresInStr = this.configService.get<string>('JWT_EXPIRES_IN', '24h');
    const expiresInSeconds = expiresInStr.endsWith('h')
      ? parseInt(expiresInStr) * 3600
      : expiresInStr.endsWith('m')
        ? parseInt(expiresInStr) * 60
        : parseInt(expiresInStr);

    const refreshToken = randomUUID();
    const expiresAt = new Date(Date.now() + 7 * 24 * 60 * 60 * 1000);

    await this.prisma.refreshToken.create({
      data: { id: randomUUID(), token: refreshToken, userId: user.id, expiresAt, createdAt: new Date() },
    });

    if (authContext?.platform && authContext?.openId) {
      await this.persistRefreshContext(refreshToken, authContext, expiresAt);
    }

    return { accessToken, refreshToken, expiresIn: expiresInSeconds };
  }

  async revokeRefreshToken(token: string): Promise<void> {
    await this.clearRefreshContext(token);
    await this.prisma.refreshToken.update({
      where: { token },
      data: { revokedAt: new Date() },
    });
  }

  private async persistRefreshContext(
    refreshToken: string,
    authContext: AuthWechatContext,
    expiresAt: Date,
  ): Promise<void> {
    const ttlSeconds = Math.max(1, Math.ceil((expiresAt.getTime() - Date.now()) / 1000));
    await this.redis.setex(
      `${REFRESH_CONTEXT_PREFIX}${refreshToken}`,
      ttlSeconds,
      JSON.stringify(authContext),
    );
  }

  private async readRefreshContext(refreshToken: string): Promise<AuthWechatContext | null> {
    const raw = await this.redis.get(`${REFRESH_CONTEXT_PREFIX}${refreshToken}`);
    if (!raw) {
      return null;
    }

    try {
      const parsed = JSON.parse(raw) as Partial<AuthWechatContext>;
      if (
        (parsed.platform === 'miniapp' || parsed.platform === 'h5')
        && typeof parsed.openId === 'string'
        && typeof parsed.appId === 'string'
      ) {
        return {
          platform: parsed.platform,
          openId: parsed.openId,
          appId: parsed.appId,
        };
      }
    } catch (_error) {
      return null;
    }

    return null;
  }

  private async clearRefreshContext(refreshToken: string): Promise<void> {
    await this.redis.del(`${REFRESH_CONTEXT_PREFIX}${refreshToken}`).catch(() => undefined);
  }

  private async readWechatH5Binding(openId: string) {
    const boundUserId = await this.redis.get(`${WECHAT_H5_BINDING_PREFIX}${openId}`);
    if (!boundUserId) {
      return null;
    }

    return this.prisma.user.findUnique({
      where: { id: boundUserId },
    });
  }

  private async persistWechatH5Binding(openId: string, userId: string) {
    await this.redis.setex(
      `${WECHAT_H5_BINDING_PREFIX}${openId}`,
      180 * 24 * 60 * 60,
      userId,
    ).catch(() => undefined);
  }

  async validateUser(userId: string) {
    const user = await this.prisma.user.findUnique({
      where: { id: userId },
      select: { id: true, username: true, email: true, displayName: true, role: true },
    });

    if (!user) throw new UnauthorizedException('User not found');
    return user;
  }

  async sendSmsCode(phone: string, type: VerificationPurpose): Promise<void> {
    const key = `sms:vcode:${phone}`;
    let verificationStored = false;

    try {
      const cooldownKey = `sms:cd:${phone}`;
      const isCooldown = await this.redis.exists(cooldownKey);
      if (isCooldown) {
        const ttl = await this.redis.ttl(cooldownKey);
        throw new BadRequestException(`请等待 ${ttl} 秒后再重新获取验证码`);
      }

      const code = `${Math.floor(100000 + Math.random() * 900000)}`;
      await this.redis.setex(key, VERIFICATION_TTL_SECONDS, JSON.stringify({ code, type }));
      verificationStored = true;

      await this.smsService.sendCode(phone, code, type);
      await this.redis.setex(cooldownKey, SEND_INTERVAL_SECONDS, '1');
    } catch (error) {
      if (verificationStored) {
        await this.redis.del(key).catch(() => undefined);
      }

      if (error instanceof HttpException) {
        throw error;
      }

      this.logger.error(
        `Failed to send SMS code for ${phone}: ${error instanceof Error ? error.message : String(error)}`,
        error instanceof Error ? error.stack : undefined,
      );
      throw new ServiceUnavailableException('验证码服务暂不可用，请稍后重试');
    }
  }

  private async verifySmsCode(phone: string, code: string): Promise<void> {
    const key = `sms:vcode:${phone}`;
    const raw = await this.redis.get(key);
    if (!raw) throw new UnauthorizedException('验证码已过期，请重新获取');
    const record = JSON.parse(raw) as { code: string; type: string };
    if (record.code !== code) throw new UnauthorizedException('验证码错误');
    await this.redis.del(key);
  }

  async registerByPhone(phone: string, smsCode: string, nickname: string, password?: string): Promise<AuthResponse> {
    await this.verifySmsCode(phone, smsCode);

    const existing = await this.prisma.user.findFirst({ where: { phone } });
    if (existing) throw new ConflictException('该手机号已注册，请直接登录');

    const base = `u${phone.slice(-4)}`;
    let username = base;
    let suffix = 0;
    while (await this.prisma.user.findFirst({ where: { username } })) {
      suffix++;
      username = `${base}${suffix}`;
    }

    const passwordHash = password ? await bcrypt.hash(password, 10) : undefined;

    const user = await this.prisma.user.create({
      data: {
        id: randomUUID(),
        username,
        phone,
        displayName: nickname || username,
        authProvider: 'phone',
        passwordHash,
        role: 'user',
        createdAt: new Date(),
        updatedAt: new Date(),
      },
      select: { id: true, username: true, phone: true, displayName: true, role: true },
    });

    const tokens = await this.generateTokens({
      id: user.id,
      username: user.username,
      displayName: user.displayName ?? user.username,
      role: user.role,
    });

    return {
      ...tokens,
      user: {
        id: user.id,
        username: user.username,
        displayName: user.displayName ?? undefined,
        role: user.role,
      },
    };
  }

  async loginByPhone(phone: string, smsCode: string): Promise<AuthResponse> {
    await this.verifySmsCode(phone, smsCode);

    const user = await this.prisma.user.findFirst({ where: { phone } });
    if (!user) throw new UnauthorizedException('该手机号尚未注册');

    const tokens = await this.generateTokens({
      id: user.id,
      username: user.username,
      displayName: user.displayName ?? user.username,
      role: user.role,
    });

    return {
      ...tokens,
      user: {
        id: user.id,
        username: user.username,
        displayName: user.displayName ?? undefined,
        role: user.role,
      },
    };
  }

  async loginByWechatMiniapp(code: string, nickname?: string, avatarUrl?: string): Promise<AuthResponse> {
    const session = await this.fetchWechatMiniappSession(code);

    let user = await this.prisma.user.findFirst({
      where: { wxOpenId: session.openid },
    });

    if (!user && session.unionid) {
      user = await this.prisma.user.findFirst({
        where: { wxUnionId: session.unionid },
      });
    }

    if (!user) {
      const username = await this.generateWechatUsername(session.openid);
      user = await this.prisma.user.create({
        data: {
          id: randomUUID(),
          username,
          displayName: nickname || username,
          avatarUrl: avatarUrl || null,
          authProvider: 'wechat',
          wxOpenId: session.openid,
          wxUnionId: session.unionid || null,
          role: 'user',
          createdAt: new Date(),
          updatedAt: new Date(),
        },
      });
    } else {
      user = await this.prisma.user.update({
        where: { id: user.id },
        data: {
          authProvider: 'wechat',
          wxOpenId: session.openid,
          wxUnionId: session.unionid || user.wxUnionId || null,
          displayName: nickname || user.displayName,
          avatarUrl: avatarUrl || user.avatarUrl || null,
        },
      });
    }

    const tokens = await this.generateTokens({
      id: user.id,
      username: user.username,
      displayName: user.displayName ?? user.username,
      role: user.role,
    }, {
      platform: 'miniapp',
      openId: session.openid,
      appId: this.getWechatMiniappAppId(),
    });

    return {
      ...tokens,
      user: {
        id: user.id,
        username: user.username,
        displayName: user.displayName ?? undefined,
        role: user.role,
      },
    };
  }

  async loginByWechatH5(code: string): Promise<AuthResponse> {
    const session = await this.fetchWechatH5AccessToken(code);
    const shouldFetchUserInfo = String(session.scope || '').includes('snsapi_userinfo');
    const profile = shouldFetchUserInfo
      ? await this.fetchWechatH5UserInfo(session.access_token, session.openid)
      : null;

    let user = session.unionid
      ? await this.prisma.user.findFirst({ where: { wxUnionId: session.unionid } })
      : null;

    if (!user) {
      user = await this.readWechatH5Binding(session.openid);
    }

    if (!user) {
      user = await this.prisma.user.findFirst({
        where: { wxOpenId: session.openid },
      });
    }

    const displayName = profile?.nickname?.trim() || '';
    const avatarUrl = profile?.headimgurl?.trim() || '';

    if (!user) {
      const username = await this.generateWechatUsername(session.openid);
      user = await this.prisma.user.create({
        data: {
          id: randomUUID(),
          username,
          displayName: displayName || username,
          avatarUrl: avatarUrl || null,
          authProvider: 'wechat',
          wxOpenId: session.openid,
          wxUnionId: session.unionid || profile?.unionid || null,
          role: 'user',
          createdAt: new Date(),
          updatedAt: new Date(),
        },
      });
    } else {
      user = await this.prisma.user.update({
        where: { id: user.id },
        data: {
          authProvider: 'wechat',
          wxUnionId: session.unionid || profile?.unionid || user.wxUnionId || null,
          ...(user.wxOpenId ? {} : { wxOpenId: session.openid }),
          ...(displayName ? { displayName } : {}),
          ...(avatarUrl ? { avatarUrl } : {}),
        },
      });
    }

    const tokens = await this.generateTokens({
      id: user.id,
      username: user.username,
      displayName: user.displayName ?? user.username,
      role: user.role,
    }, {
      platform: 'h5',
      openId: session.openid,
      appId: this.getWechatH5AppId(),
    });
    await this.persistWechatH5Binding(session.openid, user.id);

    return {
      ...tokens,
      user: {
        id: user.id,
        username: user.username,
        displayName: user.displayName ?? undefined,
        role: user.role,
      },
    };
  }
}
