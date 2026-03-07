import { Injectable, BadRequestException, UnauthorizedException, ConflictException } from '@nestjs/common';
import { JwtService } from '@nestjs/jwt';
import { ConfigService } from '@nestjs/config';
import * as bcrypt from 'bcryptjs';
import { randomUUID } from 'crypto';
import { RegisterDto, LoginDto, AuthResponse, AuthRefreshResponse } from './dto';
import { PrismaService } from '../prisma/prisma.service';

type VerificationPurpose = 'register' | 'reset_password' | 'verify';

const verificationCodeStore = new Map<
  string,
  {
    code: string;
    target: string;
    type: VerificationPurpose;
    expiresAt: Date;
  }
>();

@Injectable()
export class AuthService {
  constructor(
    private jwtService: JwtService,
    private configService: ConfigService,
    private prisma: PrismaService,
  ) {}

  async register(dto: RegisterDto): Promise<AuthResponse> {
    // Validate unique username
    const existingUsername = await this.prisma.user.findUnique({
      where: { username: dto.username.toLowerCase() },
    });

    if (existingUsername) {
      throw new ConflictException('Username already taken');
    }

    // Validate unique email if provided
    if (dto.email) {
      const existingEmail = await this.prisma.user.findUnique({
        where: { email: dto.email.toLowerCase() },
      });

      if (existingEmail) {
        throw new ConflictException('Email already registered');
      }
    }

    // Hash password
    const hashedPassword = await bcrypt.hash(dto.password, 10);

    // Create user
    const user = await this.prisma.user.create({
      data: {
        id: randomUUID(),
        username: dto.username.toLowerCase(),
        email: dto.email ? dto.email.toLowerCase() : undefined,
        phone: dto.phone || undefined,
        passwordHash: hashedPassword,
        displayName: dto.displayName || dto.username,
        role: 'user',
        createdAt: new Date(),
        updatedAt: new Date(),
      },
      select: {
        id: true,
        username: true,
        email: true,
        displayName: true,
        role: true,
      },
    });

    // Generate tokens
    const tokens = await this.generateTokens({
      id: user.id,
      username: user.username,
      email: user.email ?? undefined,
      displayName: user.displayName ?? user.username,
      role: user.role,
    });

    return {
      ...tokens,
      user: {
        id: user.id,
        username: user.username,
        email: user.email ?? undefined,
        displayName: user.displayName ?? undefined,
        role: user.role,
      },
    };
  }

  async login(dto: LoginDto): Promise<AuthResponse> {
    // Find user by email or username
    const user = await this.prisma.user.findFirst({
      where: {
        OR: [
          { email: { equals: dto.account.toLowerCase(), mode: 'insensitive' } },
          { username: { equals: dto.account.toLowerCase(), mode: 'insensitive' } },
        ],
      },
    });

    if (!user) {
      throw new UnauthorizedException('Invalid email/username or password');
    }

    if (!user.passwordHash) {
      throw new UnauthorizedException('Invalid email/username or password');
    }

    // Verify password
    const isPasswordValid = await bcrypt.compare(dto.password, user.passwordHash);

    if (!isPasswordValid) {
      throw new UnauthorizedException('Invalid email/username or password');
    }

    // Generate tokens
    const tokens = await this.generateTokens({
      id: user.id,
      username: user.username,
      email: user.email ?? undefined,
      displayName: user.displayName ?? user.username,
      role: user.role,
    });

    return {
      ...tokens,
      user: {
        id: user.id,
        username: user.username,
        email: user.email ?? undefined,
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
    const tokens = await this.generateTokens({
      id: user.id,
      username: user.username,
      email: user.email ?? undefined,
      displayName: user.displayName ?? user.username,
      role: user.role,
    });

    await this.revokeRefreshToken(token);

    return {
      accessToken: tokens.accessToken,
      refreshToken: tokens.refreshToken,
      expiresIn: tokens.expiresIn,
    };
  }

  async generateTokens(user: { id: string; username: string; email?: string; displayName: string; role: string }): Promise<{ accessToken: string; refreshToken: string; expiresIn: number }> {
    const accessToken = this.jwtService.sign(
      {
        sub: user.id,
        username: user.username,
        role: user.role,
      } as any,
      {
        secret: this.configService.get<string>('JWT_SECRET'),
        expiresIn: 900,
      },
    );

    // Generate refresh token (7 days)
    const refreshToken = randomUUID();
    const expiresAt = new Date(Date.now() + 7 * 24 * 60 * 60 * 1000);

    // Store refresh token in database
    await this.prisma.refreshToken.create({
      data: {
        id: randomUUID(),
        token: refreshToken,
        userId: user.id,
        expiresAt: expiresAt,
        createdAt: new Date(),
      },
    });

    return {
      accessToken,
      refreshToken,
      expiresIn: 15 * 60, // 15 minutes in seconds
    };
  }

  async revokeRefreshToken(token: string): Promise<void> {
    await this.prisma.refreshToken.update({
      where: { token },
      data: { revokedAt: new Date() },
    });
  }

  async validateUser(userId: string) {
    const user = await this.prisma.user.findUnique({
      where: { id: userId },
      select: {
        id: true,
        username: true,
        email: true,
        displayName: true,
        role: true,
      },
    });

    if (!user) {
      throw new UnauthorizedException('User not found');
    }

    return user;
  }

  async sendVerificationCode(target: string, type: VerificationPurpose) {
    const normalizedTarget = target.trim().toLowerCase();
    const code = `${Math.floor(100000 + Math.random() * 900000)}`;

    verificationCodeStore.set(normalizedTarget, {
      code,
      target: normalizedTarget,
      type,
      expiresAt: new Date(Date.now() + 10 * 60 * 1000),
    });

    console.log(
      `[${new Date().toISOString()}] Verification code generated for ${normalizedTarget} (${type}): ${code}`,
    );
  }

  async verifyCode(target: string, code: string) {
    const normalizedTarget = target.trim().toLowerCase();
    const record = verificationCodeStore.get(normalizedTarget);

    return {
      valid:
        Boolean(record) &&
        record?.code === code &&
        record.expiresAt.getTime() > Date.now(),
    };
  }

  async resetPassword(target: string, code: string, newPassword: string) {
    const validation = await this.verifyCode(target, code);
    if (!validation.valid) {
      throw new UnauthorizedException('Verification code is invalid or expired');
    }

    const normalizedTarget = target.trim().toLowerCase();
    const user = await this.prisma.user.findFirst({
      where: {
        OR: [
          { email: { equals: normalizedTarget, mode: 'insensitive' } },
          { phone: normalizedTarget },
        ],
      },
    });

    if (!user) {
      throw new UnauthorizedException('Account not found');
    }

    const hashedPassword = await bcrypt.hash(newPassword, 10);
    await this.prisma.user.update({
      where: { id: user.id },
      data: {
        passwordHash: hashedPassword,
        updatedAt: new Date(),
      },
    });

    verificationCodeStore.delete(normalizedTarget);
    await this.prisma.refreshToken.updateMany({
      where: { userId: user.id, revokedAt: null },
      data: { revokedAt: new Date() },
    });
  }

  async changePassword(userId: string, currentPassword: string, newPassword: string) {
    const user = await this.prisma.user.findUnique({
      where: { id: userId },
    });

    if (!user?.passwordHash) {
      throw new UnauthorizedException('Current password is incorrect');
    }

    const isPasswordValid = await bcrypt.compare(currentPassword, user.passwordHash);
    if (!isPasswordValid) {
      throw new UnauthorizedException('Current password is incorrect');
    }

    const hashedPassword = await bcrypt.hash(newPassword, 10);
    await this.prisma.user.update({
      where: { id: userId },
      data: {
        passwordHash: hashedPassword,
        updatedAt: new Date(),
      },
    });
  }
}
